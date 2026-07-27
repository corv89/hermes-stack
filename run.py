#!/usr/bin/env python3
"""Hermes pod orchestrator (Python rewrite of run.sh). Stdlib only.

Creates the `hermes` pod and starts every container in dependency order with
readiness probes. Re-runnable: it tears down and recreates the pod, but named
volumes (opencode-data, hermes-data) and bind mounts persist, so no data is lost.

Startup order (dependency-aware):
  opencode (fatal) -> sidecar (model loads in background) -> gbrain-pg -> gbrain
  -> hermes-webui (+ config restore) -> searxng/trafilatura/playwright
  -> wait for sidecar -> sourcebot

Failure policy: only `opencode` is fatal (the stack is meaningless without the
server Hermes drives). Everything else is warn-and-continue so an add-on hiccup
never blocks the core opencode + webui stack. If postgres is not ready, gbrain
is skipped (both are restart=no and cannot self-heal).
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False
    print("WARN: PyYAML not installed; falling back to overwrite behaviour",
          file=sys.stderr)

SCRIPT_DIR = Path(__file__).resolve().parent
POD = "hermes"
PROJECT_MOUNT = os.environ.get("PROJECT_MOUNT", str(Path.home() / "Src"))

# 27B model load can be slow; sidecar starts early and we wait this long for it.
SIDECAR_READY_TIMEOUT = 240

# Env keys whose values must never appear in logs.
SENSITIVE = {
    "HERMES_WEBUI_PASSWORD",
    "ZAI_API_KEY",
    "GLM_API_KEY",
    "OPENCODE_SERVER_PASS",
    "OPENCODE_SERVER_PASSWORD",
    "OPENCODE_PASSWORD",
    "POSTGRES_PASSWORD",
}

# Restored into the webui container after first boot (Z.AI provider + baked-in skill).
HERMES_CONFIG_YAML = """\
model:
  provider: zai
  default: glm-5-turbo
  base_url: https://api.z.ai/api/coding/paas/v4
skills:
  external_dirs:
    - /opt/hermes-skills
"""

# Podman secrets used across the pod, mapped to a scratch env name used only for
# the whitespace pre-flight check (so a bad one can be reported by secret name).
POD_SECRETS = [
    ("sourcebot-zai-api-key", "SB_ZAI_API_KEY"),
    ("sourcebot-keepa-api-key", "SB_KEEPA_API_KEY"),
    ("sourcebot-logfire-token", "SB_LOGFIRE_TOKEN"),
    ("gbrain-pg-password", "GB_PG_PASSWORD"),
    ("gbrain-zhipu-key", "GB_ZHIPU_KEY"),
]

# Runs inside a throwaway container: prints the scratch env names whose value has
# leading/trailing whitespace.
_SECRET_CHECK_PY = (
    "import os, sys\n"
    "bad = []\n"
    "for name in sys.argv[1:]:\n"
    "    v = os.environ.get(name, '')\n"
    "    if v and v != v.strip():\n"
    "        bad.append(name)\n"
    "print(','.join(bad))\n"
)


def log(msg: str = "") -> None:
    print(msg, flush=True)


def redact(args: list[str]) -> list[str]:
    out = []
    for a in args:
        if "=" in a:
            key, _, _val = a.partition("=")
            if key in SENSITIVE:
                a = f"{key}=***"
        out.append(a)
    return out


def run(args: list[str], check: bool = True, input_text: str | None = None,
        quiet: bool = False) -> subprocess.CompletedProcess:
    if not quiet:
        log("+ " + " ".join(shlex.quote(a) for a in redact(args)))
    r = subprocess.run(args, text=True, input=input_text, capture_output=True)
    if check and r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip()
        raise RuntimeError(f"exit {r.returncode}: {detail}")
    return r


def show_logs(name: str, tail: int = 20) -> None:
    r = run(["podman", "logs", "--tail", str(tail), name], check=False, quiet=True)
    log(((r.stdout or "") + (r.stderr or "")).rstrip())


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        env[key.strip()] = val
    return env


@dataclass
class Container:
    name: str
    image: str
    env: dict[str, str] = field(default_factory=dict)
    mounts: list[str] = field(default_factory=list)
    secrets: list[str] = field(default_factory=list)   # "name,type=env,target=X"
    devices: list[str] = field(default_factory=list)
    group_add: list[str] = field(default_factory=list)
    security_opt: list[str] = field(default_factory=list)
    cap_add: list[str] = field(default_factory=list)
    memory: str = ""
    cpus: str = ""
    restart: str = ""
    start_fatal: bool = False


def start_container(c: Container) -> bool:
    args = ["podman", "run", "-d", "--name", c.name, "--pod", POD]
    for k, v in c.env.items():
        args += ["-e", f"{k}={v}"]
    for m in c.mounts:
        args += ["-v", m]
    for s in c.secrets:
        args += ["--secret", s]
    for d in c.devices:
        args += ["--device", d]
    for g in c.group_add:
        args += ["--group-add", g]
    for so in c.security_opt:
        args += ["--security-opt", so]
    for cap in c.cap_add:
        args += ["--cap-add", cap]
    if c.memory:
        args += ["--memory", c.memory]
    if c.cpus:
        args += ["--cpus", c.cpus]
    if c.restart:
        args += ["--restart", c.restart]
    args.append(c.image)

    log(f"Starting {c.name} ...")
    try:
        r = run(args)
        cid = (r.stdout or "").strip()
        if cid:
            log(f"  {c.name} = {cid[:12]}")
        return True
    except RuntimeError as e:
        if c.start_fatal:
            log(f"ERROR: failed to start {c.name}: {e}")
            show_logs(c.name)
            sys.exit(1)
        log(f"WARN: failed to start {c.name}: {e}")
        return False


@dataclass
class Probe:
    name: str
    http: str = ""            # URL to probe (host-published, or via_container)
    via_container: str = ""   # probe pod-internal port via `podman exec <c> curl`
    pg_ready: str = ""        # container name -> pg_isready
    pg_user: str = "gbrain"
    timeout: int = 30
    interval: int = 1
    fatal: bool = False
    require_ok: bool = False  # True => require HTTP 2xx (e.g. model loaded)
    log_container: str = ""   # show these logs on fatal timeout


def probe_once(p: Probe) -> bool:
    if p.pg_ready:
        r = run(["podman", "exec", p.pg_ready, "pg_isready", "-U", p.pg_user],
                check=False, quiet=True)
        return r.returncode == 0
    if p.via_container:
        cmd = ["podman", "exec", p.via_container, "curl", "-sS", "-o", "/dev/null",
               "--max-time", "3"]
        if p.require_ok:
            cmd.append("--fail")
        cmd.append(p.http)
        r = run(cmd, check=False, quiet=True)
        return r.returncode == 0
    try:
        urllib.request.urlopen(p.http, timeout=3)
        return True
    except urllib.error.HTTPError:
        return not p.require_ok   # any HTTP response counts as "up" unless 2xx required
    except Exception:
        return False


def wait_for(p: Probe) -> bool:
    deadline = time.time() + p.timeout
    i = 0
    while time.time() < deadline:
        i += 1
        if probe_once(p):
            log(f"{p.name} up after ~{i * p.interval}s")
            return True
        time.sleep(p.interval)
    msg = f"{p.name} not responding after {p.timeout}s"
    if p.fatal:
        log(f"ERROR: {msg}")
        if p.log_container:
            show_logs(p.log_container)
        sys.exit(1)
    log(f"WARN: {msg} (continuing)")
    return False


def deep_merge_defaults(base: dict, defaults: dict) -> dict:
    for key, def_val in defaults.items():
        if key not in base:
            base[key] = def_val
        elif isinstance(def_val, dict) and isinstance(base[key], dict):
            deep_merge_defaults(base[key], def_val)
    return base


def write_hermes_config() -> None:
    log("Restoring Hermes Z.AI config ...")

    if not _HAS_YAML:
        cmd = ["podman", "exec", "-i", "hermes-webui", "bash", "-c",
               "cat > /home/hermeswebui/.hermes/config.yaml"]
        for i in range(30):
            try:
                run(cmd, input_text=HERMES_CONFIG_YAML, quiet=True)
                break
            except RuntimeError:
                if i == 29:
                    log("WARN: could not write hermes config (continuing)")
                    return
                time.sleep(1)
        log("Restarting hermes-webui to pick up config ...")
        run(["podman", "restart", "hermes-webui"], quiet=True)
        wait_for(Probe(name="hermes webui", http="http://127.0.0.1:8787/",
                       timeout=60, interval=2))
        return

    for i in range(30):
        try:
            existing_raw = run(
                ["podman", "exec", "hermes-webui", "cat",
                 "/home/hermeswebui/.hermes/config.yaml"],
                quiet=True,
            ).stdout or ""
            break
        except RuntimeError:
            if i == 29:
                log("WARN: could not read hermes config (continuing)")
                return
            time.sleep(1)

    existing = yaml.safe_load(existing_raw) or {}
    defaults = yaml.safe_load(HERMES_CONFIG_YAML) or {}
    merged = deep_merge_defaults(existing, defaults)
    merged_yaml = yaml.safe_dump(merged, default_flow_style=False, sort_keys=False)

    cmd = ["podman", "exec", "-i", "hermes-webui", "bash", "-c",
           "cat > /home/hermeswebui/.hermes/config.yaml"]
    for i in range(30):
        try:
            run(cmd, input_text=merged_yaml, quiet=True)
            break
        except RuntimeError:
            if i == 29:
                log("WARN: could not write hermes config (continuing)")
                return
            time.sleep(1)

    log("Restarting hermes-webui to pick up config ...")
    run(["podman", "restart", "hermes-webui"], quiet=True)
    wait_for(Probe(name="hermes webui", http="http://127.0.0.1:8787/",
                   timeout=60, interval=2))


def build_containers(cfg: dict[str, str]) -> dict[str, Container]:
    home = Path.home()
    ws = SCRIPT_DIR / "hermes-workspace"
    opencode_pw = cfg["OPENCODE_SERVER_PASSWORD"]
    zai = cfg["ZAI_API_KEY"]
    webui_pw = cfg["HERMES_WEBUI_PASSWORD"]
    uid, gid = os.getuid(), os.getgid()

    return {
        "opencode": Container(
            name="hermes-opencode", image="localhost/hermes-opencode:latest",
            env={"OPENCODE_PASSWORD": opencode_pw},
            mounts=[
                f"{home}/.config/opencode:/root/.config/opencode:ro,Z",
                "opencode-data:/root/.local/share/opencode",
                f"{PROJECT_MOUNT}:/work:rw,z",
                f"{ws}:/workspace:ro,z",
            ],
            start_fatal=True,
        ),
        # Started early so the 27B model loads while the rest of the stack starts.
        "sidecar": Container(
            name="sidecar", image="localhost/llamacpp-sidecar:latest",
            env={"MODEL_PATH": "/models/Qwen3.6-27B-MTP-UD-Q4_K_XL.gguf",
                 "CTX_SIZE": "16384"},
            mounts=[f"{home}/models:/models:ro"],
            devices=["/dev/kfd", "/dev/dri"], group_add=["video"],
            security_opt=["label=disable"], memory="32g", restart="always",
        ),
        "gbrain_pg": Container(
            name="hermes-gbrain-pg", image="docker.io/pgvector/pgvector:pg17",
            env={"POSTGRES_USER": "gbrain", "POSTGRES_DB": "gbrain"},
            secrets=["gbrain-pg-password,type=env,target=POSTGRES_PASSWORD"],
            mounts=["/opt/gbrain-data/pg:/var/lib/postgresql/data:Z"],
            memory="1g", cpus="1",
        ),
        "gbrain": Container(
            name="hermes-gbrain", image="localhost/gbrain:latest",
            env={"DATABASE_HOST": "localhost", "DATABASE_PORT": "5432",
                 "DATABASE_USER": "gbrain", "DATABASE_NAME": "gbrain",
                 "GBRAIN_PORT": "8083", "GBRAIN_HOME": "/root/.gbrain",
                 "GBRAIN_FTS_LANGUAGE": "english"},
            secrets=["gbrain-pg-password,type=env,target=POSTGRES_PASSWORD",
                     "gbrain-zhipu-key,type=env,target=ZHIPUAI_API_KEY"],
            mounts=["/opt/gbrain-data/config:/root/.gbrain:Z",
                    "/opt/gbrain-data/brain:/root/brain:Z"],
            memory="2g", cpus="1",
        ),
        "webui": Container(
            name="hermes-webui", image="localhost/hermes-webui:latest",
            env={
                "HERMES_WEBUI_HOST": "0.0.0.0", "HERMES_WEBUI_PORT": "8787",
                "HERMES_WEBUI_STATE_DIR": "/home/hermeswebui/.hermes/webui",
                "HERMES_WEBUI_AUTO_INSTALL": "1",
                "HERMES_WEBUI_AGENT_DIR": "/usr/local/lib/hermes-agent",
                "HERMES_WEBUI_PASSWORD": webui_pw,
                "WANTED_UID": str(uid), "WANTED_GID": str(gid),
                "OPENCODE_SERVER_URL": "http://127.0.0.1:45650",
                "OPENCODE_SERVER_USER": "opencode",
                "OPENCODE_SERVER_PASS": opencode_pw,
                "OPENCODE_SERVER_PASSWORD": opencode_pw,
                "OPENCODE_PASSWORD": opencode_pw,
                "GLM_API_KEY": zai, "ZAI_API_KEY": zai,
                "SEARXNG_URL": "http://localhost:8080",
                "TRAFILATURA_URL": "http://localhost:8000",
                "PLAYWRIGHT_URL": "http://localhost:8001",
            },
            mounts=[
                "hermes-data:/home/hermeswebui/.hermes",
                f"{PROJECT_MOUNT}:/work:ro,z",
                f"{ws}:/workspace:rw,z",
            ],
            start_fatal=True,
        ),
        "searxng": Container(
            name="hermes-searxng", image="docker.io/searxng/searxng:latest",
            mounts=[f"{SCRIPT_DIR}/web-tools/searxng/settings.yml:/etc/searxng/settings.yml:ro,Z"],
        ),
        "trafilatura": Container(
            name="hermes-trafilatura", image="localhost/hermes-trafilatura:latest",
        ),
        "playwright": Container(
            name="hermes-playwright", image="localhost/hermes-playwright:latest",
            cap_add=["SYS_ADMIN"],
        ),
        "sourcebot": Container(
            name="sourcebot", image="localhost/sourcebot:latest",
            env={"SEARXNG_URL": "http://localhost:8080",
                 "TRAFILATURA_URL": "http://localhost:8000",
                 "PLAYWRIGHT_URL": "http://localhost:8001"},
            secrets=["sourcebot-zai-api-key,type=env,target=ZAI_API_KEY",
                     "sourcebot-keepa-api-key,type=env,target=KEEPA_API_KEY",
                     "sourcebot-logfire-token,type=env,target=LOGFIRE_TOKEN"],
            mounts=[f"{home}/Src/sourcebot/data:/app/data",
                    f"{home}/Src/sourcebot/config:/app/config:ro"],
            memory="4g", cpus="2", restart="always",
        ),
    }


def banner() -> None:
    log()
    log("=== Pod started ===")
    log("WebUI:       http://127.0.0.1:8787")
    log("Sourcebot:   http://127.0.0.1:8181")
    log("Tailscale:   https://bigbox.kamori-eel.ts.net        (Hermes :443)")
    log("             https://bigbox.kamori-eel.ts.net:8443   (Sourcebot)")
    log("SearXNG:     http://127.0.0.1:8888")
    log("Trafilatura: http://127.0.0.1:8100")
    log("Playwright:  http://127.0.0.1:8101")
    log("Password:    (see .env)")
    log()
    r = run(["podman", "pod", "ps", "--filter", f"name={POD}"], quiet=True)
    log((r.stdout or "").rstrip())


def check_secrets_whitespace() -> None:
    """Pre-flight guard: warn if any pod secret has leading/trailing whitespace.

    Secrets created with `echo "$VAL" | podman secret create NAME -` carry a
    trailing newline; external APIs (e.g. Keepa) then reject the key
    (REQUEST_REJECTED) while it looks like an empty/expired token. Recreate
    cleanly with `printf '%s' "$VAL" | podman secret create NAME -`.
    """
    args = ["podman", "run", "--rm"]
    mounted: list[tuple[str, str]] = []
    for secret, target in POD_SECRETS:
        if run(["podman", "secret", "exists", secret], check=False, quiet=True).returncode == 0:
            args += ["--secret", f"{secret},type=env,target={target}"]
            mounted.append((secret, target))
    if not mounted:
        return
    targets = [target for _, target in mounted]
    args += ["localhost/hermes-opencode:latest", "python3", "-c", _SECRET_CHECK_PY, *targets]
    r = run(args, check=False, quiet=True)
    if r.returncode != 0:
        log(f"WARN: secret whitespace pre-flight could not run (exit {r.returncode}); skipping")
        return
    target_to_secret = {target: secret for secret, target in mounted}
    bad = [target_to_secret[t] for t in (r.stdout or "").strip().split(",") if t in target_to_secret]
    if bad:
        log("WARN: secret(s) with leading/trailing whitespace "
            f"(likely `echo` vs `printf '%s'`): {', '.join(bad)}")
        log("      A trailing newline makes external API keys get rejected. Recreate cleanly:")
        log('      printf \'%s\' "$VALUE" | podman secret create <name> -   # then recreate the container')


def build_images() -> None:
    log('Building container images ...')
    run(['podman', 'build', '-f', 'opencode.Containerfile', '-t', 'localhost/hermes-opencode:latest', str(SCRIPT_DIR)])
    run(['podman', 'build', '-f', str(SCRIPT_DIR / 'web-tools/trafilatura/Containerfile'), '-t', 'localhost/hermes-trafilatura:latest', str(SCRIPT_DIR / 'web-tools/trafilatura')])
    run(['podman', 'build', '-f', str(SCRIPT_DIR / 'web-tools/playwright/Containerfile'), '-t', 'localhost/hermes-playwright:latest', str(SCRIPT_DIR / 'web-tools/playwright')])
    run(['podman', 'pull', 'docker.io/searxng/searxng:latest'])
    run(['podman', 'build', '-f', 'hermes.Containerfile', '-t', 'localhost/hermes-webui:latest', str(SCRIPT_DIR)])


def main() -> None:
    if '--no-build' not in sys.argv:
        build_images()

    cfg = load_env(SCRIPT_DIR / ".env")
    for key in ("OPENCODE_SERVER_PASSWORD", "HERMES_WEBUI_PASSWORD", "ZAI_API_KEY"):
        if not cfg.get(key):
            log(f"ERROR: {key} must be set in .env")
            sys.exit(1)

    # Pre-flight: warn early if any pod secret has trailing whitespace (the `echo`
    # vs `printf '%s'` bug that makes external API keys get rejected).
    check_secrets_whitespace()

    (SCRIPT_DIR / "hermes-workspace").mkdir(exist_ok=True)

    # --- cleanup + create pod ------------------------------------------------
    if run(["podman", "pod", "exists", POD], check=False, quiet=True).returncode == 0:
        log(f"Stopping + removing existing pod {POD} ...")
        run(["podman", "pod", "stop", POD], check=False, quiet=True)
        run(["podman", "pod", "rm", "-f", POD], check=False, quiet=True)

    log(f"Creating pod {POD} (webui :8787, searxng :8888, trafilatura :8100, "
        f"playwright :8101, sourcebot :8181) ...")
    run(["podman", "pod", "create", "--name", POD,
         "-p", "127.0.0.1:8787:8787",
         "-p", "127.0.0.1:8888:8080",
         "-p", "127.0.0.1:8100:8000",
         "-p", "127.0.0.1:8101:8001",
         "-p", "127.0.0.1:8181:8181"])

    c = build_containers(cfg)

    # 1. opencode (fatal) — Hermes drives this server. 45650 is pod-internal
    #    (not host-published), so probe from inside the container, like the old bash.
    start_container(c["opencode"])
    wait_for(Probe(name="opencode", http="http://127.0.0.1:45650/",
                   via_container="hermes-opencode", timeout=30,
                   fatal=True, log_container="hermes-opencode"))
    log("opencode creds: user=opencode (stable password from .env)")

    # 2. sidecar — start early; the model loads in the background.
    start_container(c["sidecar"])

    # 3. gbrain-pg -> gbrain (restart=no; gbrain needs postgres up first).
    start_container(c["gbrain_pg"])
    if wait_for(Probe(name="gbrain-pg", pg_ready="hermes-gbrain-pg",
                      pg_user="gbrain", timeout=30)):
        start_container(c["gbrain"])
        wait_for(Probe(name="gbrain", http="http://127.0.0.1:8083/",
                       via_container="hermes-opencode", timeout=30))
    else:
        log("WARN: skipping gbrain (postgres not ready)")

    # 4. hermes-webui (needs opencode + gbrain) + config restore.
    start_container(c["webui"])
    write_hermes_config()

    # 5. web-tools (non-fatal).
    start_container(c["searxng"])
    start_container(c["trafilatura"])
    start_container(c["playwright"])
    wait_for(Probe(name="SearXNG",
                   http="http://127.0.0.1:8888/search?q=test&format=json", timeout=60))
    wait_for(Probe(name="Trafilatura", http="http://127.0.0.1:8100/health", timeout=30))
    wait_for(Probe(name="Playwright", http="http://127.0.0.1:8101/health", timeout=30))

    # 6. wait for the sidecar model to load before starting sourcebot.
    wait_for(Probe(name="sidecar", http="http://127.0.0.1:8090/health",
                   via_container="hermes-opencode", timeout=SIDECAR_READY_TIMEOUT,
                   interval=3, require_ok=True))

    # 7. sourcebot (needs web-tools + sidecar).
    start_container(c["sourcebot"])
    wait_for(Probe(name="sourcebot", http="http://127.0.0.1:8181/", timeout=60))

    banner()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("\ninterrupted")
        sys.exit(130)
    except RuntimeError as e:
        log(f"ERROR: {e}")
        sys.exit(1)
