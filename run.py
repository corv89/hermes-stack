#!/usr/bin/env python3
"""Hermes pod orchestrator. Stdlib only.

Creates the `hermes` pod and starts every container in dependency order with
readiness probes. Re-runnable: it tears down and recreates the pod, but named
volumes (opencode-data, hermes-data) and bind mounts persist, so no data is lost.

Startup order (dependency-aware):
  opencode (fatal) -> sidecar (model loads in background)
  -> llama-embed (CPU) -> llama-rerank (GPU) -> gbrain-pg -> gbrain
  -> hermes-webui (+ config restore) -> searxng/trafilatura/playwright
  -> wait for sidecar -> sourcebot

Failure policy: only `opencode` is fatal (the stack is meaningless without the
server Hermes drives). Everything else is warn-and-continue so an add-on hiccup
never blocks the core opencode + webui stack. If the embed server or postgres is
not ready, gbrain is skipped (both are restart=no and cannot self-heal).

gbrain stack (fully local, no external API keys):
  hermes-llama-embed   :8084  llama.cpp --embeddings (CPU, Qwen3-Embedding-4B, 2560d)
  hermes-llama-rerank  :8085  llama.cpp --reranking  (GPU, Qwen3-Reranker-4B)
  hermes-gbrain-pg     :5432  Postgres 17 + pgvector
  hermes-gbrain        :8083  gbrain MCP HTTP server
"""
from __future__ import annotations

import json
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

GBRAIN_DATA_DIR = Path(os.environ.get("GBRAIN_DATA_DIR", "/opt/gbrain-data"))

# 27B model load can be slow; sidecar starts early and we wait this long for it.
SIDECAR_READY_TIMEOUT = 240

# Model files for gbrain's local embedding/reranking servers.
EMBED_MODEL_FILE = "Qwen3-Embedding-4B-Q4_K_M.gguf"
EMBED_MODEL_URL = (
    "https://huggingface.co/Qwen/Qwen3-Embedding-4B-GGUF/resolve/main/"
    "Qwen3-Embedding-4B-Q4_K_M.gguf"
)
RERANK_MODEL_FILE = "Qwen3-Reranker-4B-Q4_K_M.gguf"
RERANK_MODEL_URL = (
    "https://huggingface.co/DevQuasar/Qwen.Qwen3-Reranker-4B-GGUF/resolve/main/"
    "Qwen.Qwen3-Reranker-4B.Q4_K_M.gguf"
)

LLAMA_IMAGE_CPU = "ghcr.io/ggml-org/llama.cpp:server"
LLAMA_IMAGE_GPU = "ghcr.io/ggml-org/llama.cpp:server-rocm"
# gfx1201/RDNA4-capable build: the official :server-rocm image's ROCm is too
# old to see this GPU, so the reranker reuses the same custom image as the sidecar.
LLAMA_IMAGE_ROCM = "localhost/llamacpp-sidecar:latest"

# Env keys whose values must never appear in logs.
SENSITIVE = {
    "HERMES_WEBUI_PASSWORD",
    "ZAI_API_KEY",
    "GLM_API_KEY",
    "OPENCODE_SERVER_PASS",
    "OPENCODE_SERVER_PASSWORD",
    "OPENCODE_PASSWORD",
    "POSTGRES_PASSWORD",
    "GBRAIN_ADMIN_BOOTSTRAP_TOKEN",
    "GBRAIN_ADMIN_TOKEN",
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
mcp_servers:
  gbrain:
    url: http://localhost:8083/mcp
    headers:
      Authorization: "Bearer {gbrain_token}"
"""

# Podman secrets used across the pod, mapped to a scratch env name used only for
# the whitespace pre-flight check (so a bad one can be reported by secret name).
POD_SECRETS = [
    ("sourcebot-zai-api-key", "SB_ZAI_API_KEY"),
    ("sourcebot-keepa-api-key", "SB_KEEPA_API_KEY"),
    ("sourcebot-logfire-token", "SB_LOGFIRE_TOKEN"),
    ("gbrain-pg-password", "GB_PG_PASSWORD"),
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


# Persisted DCR client credentials for the gbrain MCP server (host-side). The
# access token is minted fresh each run (client_credentials, long TTL) and
# injected into the Hermes config as a Bearer header.
GBRAIN_CLIENT_STATE = GBRAIN_DATA_DIR / "config" / ".mcp-client.json"

# Runs inside the pod (hermes-opencode shares the pod netns with gbrain): reads
# client creds from stdin (or registers a new DCR client), mints a
# client_credentials access token, prints {client_id, client_secret,
# access_token} as JSON (or {"error": ...}) on stdout.
_GBRAIN_MINT_PY = r'''
import json, sys, urllib.request, urllib.parse
BASE = "http://localhost:8083"
def post(path, data, form=False):
    body = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
    ctype = "application/x-www-form-urlencoded" if form else "application/json"
    req = urllib.request.Request(BASE + path, data=body, method="POST")
    req.add_header("Content-Type", ctype)
    req.add_header("Accept", "application/json, text/event-stream")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode() or "{}")
try:
    raw = sys.stdin.read().strip()
    creds = json.loads(raw) if raw else None
    if not creds:
        reg = post("/register", {
            "client_name": "hermes",
            "redirect_uris": ["http://localhost/callback"],
            "grant_types": ["client_credentials"],
            "token_endpoint_auth_method": "client_secret_basic",
            "scope": "admin",
        })
        creds = {"client_id": reg["client_id"], "client_secret": reg["client_secret"]}
    tok = post("/token", {
        "grant_type": "client_credentials",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "scope": "admin",
    }, form=True)
    print(json.dumps({"client_id": creds["client_id"],
                      "client_secret": creds["client_secret"],
                      "access_token": tok["access_token"]}))
except Exception as exc:
    print(json.dumps({"error": str(exc)}))
'''


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
    cmd: list[str] = field(default_factory=list)  # args appended after image


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
    args += c.cmd

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


def write_hermes_config(gbrain_access: str) -> None:
    log("Restoring Hermes Z.AI config ...")
    config_yaml = HERMES_CONFIG_YAML.format(gbrain_token=gbrain_access or "MISSING")

    if not _HAS_YAML:
        cmd = ["podman", "exec", "-i", "hermes-webui", "bash", "-c",
               "cat > /home/hermeswebui/.hermes/config.yaml"]
        for i in range(30):
            try:
                run(cmd, input_text=config_yaml, quiet=True)
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
    defaults = yaml.safe_load(config_yaml) or {}
    merged = deep_merge_defaults(existing, defaults)
    # Force the gbrain MCP entry so a freshly-minted access token always wins
    # (deep_merge only fills missing keys; it would keep a stale bearer).
    if gbrain_access:
        merged["mcp_servers"] = merged.get("mcp_servers") or {}
        merged["mcp_servers"]["gbrain"] = {
            "url": "http://localhost:8083/mcp",
            "headers": {"Authorization": f"Bearer {gbrain_access}"},
        }
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


def download_models() -> None:
    """Download GGUF model files for embedding/reranker if not present."""
    models_dir = GBRAIN_DATA_DIR / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    for name, url in [(EMBED_MODEL_FILE, EMBED_MODEL_URL),
                      (RERANK_MODEL_FILE, RERANK_MODEL_URL)]:
        dest = models_dir / name
        if dest.exists():
            log(f"  {name} already present")
            continue
        log(f"  Downloading {name} ...")
        r = subprocess.run(
            ["curl", "-L", "--progress-bar", "-o", str(dest), url],
        )
        if r.returncode != 0:
            raise RuntimeError(f"failed to download {name}")


def build_containers(cfg: dict[str, str]) -> dict[str, Container]:
    home = Path.home()
    ws = SCRIPT_DIR / "hermes-workspace"
    opencode_pw = cfg["OPENCODE_SERVER_PASSWORD"]
    zai = cfg["ZAI_API_KEY"]
    webui_pw = cfg["HERMES_WEBUI_PASSWORD"]
    gbrain_token = cfg["GBRAIN_ADMIN_TOKEN"]
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
        # gbrain embedding server — CPU, Qwen3-Embedding-4B (2560d; HNSW caps at 4000).
        "llama_embed": Container(
            name="hermes-llama-embed",
            image=LLAMA_IMAGE_CPU,
            mounts=[f"{GBRAIN_DATA_DIR}/models:/models:Z"],
            security_opt=["label=disable"],
            memory="8g", cpus="16",
            cmd=[
                "--model", f"/models/{EMBED_MODEL_FILE}",
                "--alias", "Qwen3-Embedding-4B",
                "--embeddings",
                "--host", "0.0.0.0",
                "--port", "8084",
                "--n-gpu-layers", "0",
                "--threads", "16",
            ],
        ),
        # gbrain reranker — GPU, Qwen3-Reranker-4B.
        "llama_rerank": Container(
            name="hermes-llama-rerank",
            image=LLAMA_IMAGE_ROCM,
            devices=["/dev/kfd", "/dev/dri"], group_add=["video"],
            security_opt=["label=disable"],
            mounts=[f"{GBRAIN_DATA_DIR}/models:/models:Z"],
            memory="8g",
            cmd=[
                "llama-server",
                "--model", f"/models/{RERANK_MODEL_FILE}",
                "--alias", "Qwen3-Reranker-4B",
                "--reranking",
                "--host", "0.0.0.0",
                "--port", "8085",
                "--n-gpu-layers", "99",
                "--threads", "4",
            ],
        ),
        "gbrain_pg": Container(
            name="hermes-gbrain-pg", image="docker.io/pgvector/pgvector:pg17",
            env={"POSTGRES_USER": "gbrain", "POSTGRES_DB": "gbrain"},
            secrets=["gbrain-pg-password,type=env,target=POSTGRES_PASSWORD"],
            mounts=[f"{GBRAIN_DATA_DIR}/pg:/var/lib/postgresql/data:Z"],
            memory="1g", cpus="1",
        ),
        "gbrain": Container(
            name="hermes-gbrain", image="localhost/gbrain:latest",
            env={
                "DATABASE_HOST": "localhost", "DATABASE_PORT": "5432",
                "DATABASE_USER": "gbrain", "DATABASE_NAME": "gbrain",
                "GBRAIN_PORT": "8083", "GBRAIN_HOME": "/root",
                "GBRAIN_FTS_LANGUAGE": "english",
                "LLAMA_SERVER_BASE_URL": "http://localhost:8084/v1",
                "LLAMA_SERVER_RERANKER_BASE_URL": "http://localhost:8085/v1",
                "EMBED_ALIAS": "Qwen3-Embedding-4B",
                "EMBED_DIMENSIONS": "2560",
                "RERANK_ALIAS": "Qwen3-Reranker-4B",
                "GBRAIN_EMBEDDING_MODEL": "llama-server:Qwen3-Embedding-4B",
                "GBRAIN_EMBEDDING_DIMENSIONS": "2560",
                "GBRAIN_ADMIN_BOOTSTRAP_TOKEN": gbrain_token,
            },
            secrets=["gbrain-pg-password,type=env,target=POSTGRES_PASSWORD"],
            mounts=[f"{GBRAIN_DATA_DIR}/config:/root/.gbrain:Z",
                    f"{GBRAIN_DATA_DIR}/brain:/root/brain:Z"],
            memory="2g", cpus="2",
        ),
        "webui": Container(
            name="hermes-webui", image="localhost/hermes-webui:latest",
            env={
                "HERMES_WEBUI_HOST": "0.0.0.0", "HERMES_WEBUI_PORT": "8787",
                "UV_LINK_MODE": "copy",
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
            mounts=[f"{SCRIPT_DIR}/config/searxng/settings.yml:/etc/searxng/settings.yml:ro,Z"],
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
    log("gbrain MCP:  http://127.0.0.1:8083/mcp")
    log("Embeddings:  http://127.0.0.1:8084/v1  (Qwen3-Embedding-4B, CPU, 2560d)")
    log("Reranker:    http://127.0.0.1:8085/v1  (Qwen3-Reranker-4B, GPU)")
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


def ensure_gbrain_mcp_token() -> str:
    # Register a DCR client once (creds persist on the host) and mint a
    # long-lived client_credentials access token for the gbrain MCP endpoint.
    creds = ""
    if GBRAIN_CLIENT_STATE.exists():
        creds = GBRAIN_CLIENT_STATE.read_text().strip()

    def mint(seed: str) -> dict:
        r = run(["podman", "exec", "-i", "hermes-opencode", "python3", "-c", _GBRAIN_MINT_PY],
                input_text=seed, quiet=True)
        lines = [ln for ln in (r.stdout or "").strip().splitlines() if ln.strip()]
        return json.loads(lines[-1]) if lines else {"error": "no output"}

    out = mint(creds)
    if "access_token" not in out and creds:
        out = mint("")  # stored client invalid (e.g. DB wiped) -> re-register
    if "access_token" not in out:
        raise RuntimeError(f"gbrain MCP token mint failed: {out.get('error')}")
    GBRAIN_CLIENT_STATE.write_text(json.dumps(
        {"client_id": out["client_id"], "client_secret": out["client_secret"]}))
    return out["access_token"]


def build_images() -> None:
    log('Building container images ...')
    run(['podman', 'build', '-f', str(SCRIPT_DIR / 'images/opencode/Containerfile'), '-t', 'localhost/hermes-opencode:latest', str(SCRIPT_DIR)])
    run(['podman', 'build', '-f', str(SCRIPT_DIR / 'images/trafilatura/Containerfile'), '-t', 'localhost/hermes-trafilatura:latest', str(SCRIPT_DIR)])
    run(['podman', 'build', '-f', str(SCRIPT_DIR / 'images/playwright/Containerfile'), '-t', 'localhost/hermes-playwright:latest', str(SCRIPT_DIR)])
    run(['podman', 'pull', 'docker.io/searxng/searxng:latest'])
    run(['podman', 'build', '-f', str(SCRIPT_DIR / 'images/webui/Containerfile'), '-t', 'localhost/hermes-webui:latest', str(SCRIPT_DIR)])
    # gbrain image
    run(['podman', 'build', '-f', str(SCRIPT_DIR / 'images/gbrain/Containerfile'),
         '-t', 'localhost/gbrain:latest', str(SCRIPT_DIR)])
    # llama.cpp images (CPU + GPU)
    run(['podman', 'pull', LLAMA_IMAGE_CPU])
    run(['podman', 'pull', LLAMA_IMAGE_GPU])


def main() -> None:
    if '--no-build' not in sys.argv:
        build_images()

    cfg = load_env(SCRIPT_DIR / ".env")
    for key in ("OPENCODE_SERVER_PASSWORD", "HERMES_WEBUI_PASSWORD", "ZAI_API_KEY", "GBRAIN_ADMIN_TOKEN"):
        if not cfg.get(key):
            log(f"ERROR: {key} must be set in .env")
            sys.exit(1)

    # Pre-flight: warn early if any pod secret has trailing whitespace (the `echo`
    # vs `printf '%s'` bug that makes external API keys get rejected).
    check_secrets_whitespace()

    (SCRIPT_DIR / "hermes-workspace").mkdir(exist_ok=True)

    # Ensure gbrain data dirs exist.
    for subdir in ("pg", "config", "brain", "models"):
        (GBRAIN_DATA_DIR / subdir).mkdir(parents=True, exist_ok=True)

    # Download GGUF model files for embedding/reranker if not present.
    log("Checking gbrain model files ...")
    download_models()

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
         "-p", "127.0.0.1:8181:8181",
         "-p", "127.0.0.1:8084:8084",
         "-p", "127.0.0.1:8085:8085"])

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

    # 3. gbrain stack: embed server -> rerank server -> postgres -> gbrain
    #    (restart=no for all; if a dependency fails, skip downstream).

    # 3a. Embedding server (CPU, ~4.7GB model — slow to load).
    start_container(c["llama_embed"])
    embed_up = wait_for(Probe(
        name="llama-embed", http="http://127.0.0.1:8084/health",
        timeout=120, interval=3))

    # 3b. Reranker server (GPU).
    if embed_up:
        start_container(c["llama_rerank"])
        rerank_up = wait_for(Probe(
            name="llama-rerank", http="http://127.0.0.1:8085/health",
            timeout=120, interval=3))
    else:
        rerank_up = False
        log("WARN: skipping reranker (embed server not up)")

    # 3c. Postgres + pgvector.
    start_container(c["gbrain_pg"])
    pg_up = wait_for(Probe(name="gbrain-pg", pg_ready="hermes-gbrain-pg",
                           pg_user="gbrain", timeout=30))

    # 3d. gbrain MCP server.
    gbrain_up = False
    if pg_up and embed_up:
        start_container(c["gbrain"])
        gbrain_up = wait_for(Probe(name="gbrain", http="http://127.0.0.1:8083/",
                                   via_container="hermes-opencode", timeout=120))
    else:
        log("WARN: skipping gbrain (postgres or embeddings not ready)")

    # 3e. Mint a long-lived MCP access token for Hermes (needs gbrain up).
    gbrain_access = ""
    if gbrain_up:
        try:
            gbrain_access = ensure_gbrain_mcp_token()
            log("gbrain MCP access token minted (client_credentials, 1y TTL)")
        except RuntimeError as e:
            log(f"WARN: {e} (Hermes will not have gbrain tools)")

    # 4. hermes-webui (needs opencode + gbrain) + config restore.
    start_container(c["webui"])
    write_hermes_config(gbrain_access)

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
