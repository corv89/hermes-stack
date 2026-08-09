#!/usr/bin/env python3
"""Hermes stack orchestrator — Quadlet edition. Stdlib only.

Renders quadlet/*.container|.network|.service templates (substituting runtime
values like GPU devices and secrets) into ~/.config/containers/systemd/ and
drives the stack through systemd user units. The shared pod is retired: every
container runs standalone on the `hermesnet` bridge network with container-name
DNS (hermes-opencode, hermes-gbrain, hermes-sidecar, ...).

Permissions: hermes-webui runs with UserNS=keep-id, so container uid 1000 IS
the host user — everything the agent writes into the hermes-data volume is
natively host-owned and editable without any FUSE/bindfs shim.

Boot persistence: quadlet generates the units into default.target on every
daemon-reload (user linger enabled) — the stack comes up at boot with no
hand-written service.

Usage:
  run.py                  install/refresh units + start the stack + gates
  run.py --install        render + install units only (no start)
  run.py --render         print rendered units to stdout (review/diff)
  run.py --stop           stop all units (containers kept)
  run.py --status         unit + container status
  run.py --config-sync    idempotent gbrain MCP token + config.yaml sync
  run.py --redeploy       stop, remove containers, start fresh (image updates)
  run.py --build          (re)build all images
  run.py --build-sidecar  rebuild the ROCm sidecar image + recreate its unit

Startup order (systemd-ordered + readiness gates):
  opencode (fatal) -> sidecar (model loads in background)
  -> llama-embed + llama-rerank (Vulkan on Vega 56) -> gbrain-pg -> gbrain
  -> config-sync (mints the gbrain MCP token, merges config.yaml)
  -> hermes-webui -> searxng/trafilatura/playwright -> sourcebot

Failure policy: only opencode + webui are fatal (the core stack). Everything
else is warn-and-continue so an add-on hiccup never blocks it.

GPU layout (dual-GPU host):
  Vega 56 (gfx900, 8GB)   - embed + rerank via the llama.cpp Vulkan backend
  R9700   (gfx1201, 32GB) - sidecar 27B LLM via ROCm (pinned via
                            ROCR_VISIBLE_DEVICES: a ROCm runtime supports only
                            one GPU generation, so it must not see the Vega)
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
from pathlib import Path

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False
    print("WARN: PyYAML not installed; config sync falls back to overwrite",
          file=sys.stderr)

SCRIPT_DIR = Path(__file__).resolve().parent
QUADLET_SRC = SCRIPT_DIR / "quadlet"
UNIT_DIR = Path.home() / ".config" / "containers" / "systemd"
# Plain systemd units (non-quadlet) live in the regular user unit dir.
SYSTEMD_UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
PROJECT_MOUNT = os.environ.get("PROJECT_MOUNT", str(Path.home() / "Src"))
GBRAIN_DATA_DIR = Path(os.environ.get("GBRAIN_DATA_DIR", "/opt/gbrain-data"))
HERMES_DATA_VOL = (Path.home() /
                   ".local/share/containers/storage/volumes/hermes-data/_data")

# 27B model load can be slow; we wait this long for the sidecar health gate.
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
WHISPER_MODEL_FILE = "ggml-large-v3.bin"
WHISPER_MODEL_URL = (
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
    "ggml-large-v3.bin"
)

LLAMA_IMAGE_CPU = "ghcr.io/ggml-org/llama.cpp:server"          # CPU fallback
# Vulkan backend for the aux servers on the Vega 56 (gfx900): ROCm dropped
# gfx900 support, but Vulkan (RADV) still handles it. The official image ships
# mesa-vulkan-drivers, so the container only needs the Vega's render node.
LLAMA_IMAGE_VULKAN = "ghcr.io/ggml-org/llama.cpp:server-vulkan"
# gfx1201/RDNA4-capable build: the official :server-rocm image's ROCm is too
# old to see the R9700, so the sidecar uses a custom image — recipe in
# images/sidecar/Containerfile (rebuild: `run.py --build-sidecar`).
LLAMA_IMAGE_ROCM = "localhost/llamacpp-sidecar:latest"

# --- GPU topology (dual-GPU host) ---------------------------------------------
GPU_PCI_VULKAN = "0000:06:00.0"   # Vega 56 (gfx900)
GPU_PCI_ROCM = "0000:0e:00.0"     # R9700 (gfx1201)
GPU_GFX_ROCM = 120001             # KFD gfx_target_version of the R9700


def render_node(pci_addr: str) -> str:
    """DRM render node for a GPU, resolved from its stable by-path symlink."""
    link = Path(f"/dev/dri/by-path/pci-{pci_addr}-render")
    if not link.exists():
        raise RuntimeError(f"no render node for GPU {pci_addr} ({link} missing)")
    return os.path.realpath(link)


def rocm_agent_index(gfx_target: int) -> str:
    """GPU agent index (for ROCR_VISIBLE_DEVICES) of a gfx target in the KFD
    topology. The CPU node (gfx_target_version 0) is skipped."""
    nodes = Path("/sys/class/kfd/kfd/topology/nodes")
    idx = 0
    for node in sorted(nodes.iterdir(), key=lambda n: int(n.name)):
        props = node / "properties"
        if not props.exists():
            continue
        gfx = 0
        for line in props.read_text().splitlines():
            if line.startswith("gfx_target_version"):
                gfx = int(line.split()[1])
                break
        if gfx == 0:
            continue
        if gfx == gfx_target:
            return str(idx)
        idx += 1
    raise RuntimeError(f"no KFD GPU agent with gfx_target_version {gfx_target}")


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

# Deep-merged into the Hermes config.yaml on the hermes-data volume (the
# {gbrain_token} placeholder is replaced with a freshly minted MCP token).
#
# Model topology — LOCAL-FIRST with cloud escalation:
#   * Primary is the on-box ROCm sidecar (Qwen3.6-27B on the R9700), exposed
#     as an OpenAI-compatible endpoint and registered as a named custom
#     provider `sidecar`. model.provider: custom:sidecar selects it.
#   * fallback_providers is Hermes' ordered failover chain, tried when the
#     primary errors (rate-limit / overload / connection). The cloud zai/GLM
#     endpoint stays wired as the escalation target so an outage, a cold
#     sidecar (model still loading), or a 429 transparently escalates to
#     cloud compute. api_max_retries is lowered for fast failover.
#   * Manual escalation is also available at runtime via the gateway
#     `/model <name> --provider zai` slash command.
# NOTE: this template is run through str.format(), so it must not contain
# literal '{' or '}' (YAML block style below avoids flow-mapping braces).
HERMES_CONFIG_YAML = """\
providers:
  sidecar:
    base_url: http://hermes-sidecar:8090/v1
model:
  provider: custom:sidecar
  default: qwen3.6-27b
  # Explicit (matches providers.sidecar): deep-merge never deletes keys, so
  # this clobbers the stale cloud base_url left over from the zai-primary era.
  base_url: http://hermes-sidecar:8090/v1
  # Must match the sidecar's unified KV pool (CTX_SIZE) and clear Hermes'
  # 64K minimum-context gate (agent_init MINIMUM_CONTEXT_LENGTH).
  context_length: 65536
agent:
  api_max_retries: 1
fallback_providers:
  - provider: zai
    model: glm-5-turbo
    base_url: https://api.z.ai/api/coding/paas/v4
skills:
  external_dirs:
    - /opt/hermes-skills
streaming:
  enabled: true
auxiliary:
  vision:
    provider: custom
    model: qwen3.7-plus
    base_url: https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic/v1
    api_mode: anthropic_messages
    api_key: {alibaba_key}
  web_extract:
    provider: custom
    model: qwen3.6-flash
    base_url: https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic/v1
    api_key: {alibaba_key}
  compression:
    provider: custom
    model: qwen3.7-max
    base_url: https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic/v1
    api_key: {alibaba_key}
  approval:
    provider: custom
    model: qwen3.7-max
    base_url: https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic/v1
    api_key: {alibaba_key}
  mcp:
    provider: custom
    model: qwen3.7-max
    base_url: https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic/v1
    api_key: {alibaba_key}
  title_generation:
    provider: custom
    model: qwen3.6-flash
    base_url: https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic/v1
    api_key: {alibaba_key}
  skills_hub:
    provider: custom
    model: qwen3.6-flash
    base_url: https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic/v1
    api_key: {alibaba_key}
  curator:
    provider: custom
    model: qwen3.6-flash
    base_url: https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic/v1
    api_key: {alibaba_key}
  kanban_decomposer:
    provider: custom
    model: qwen3.7-max
    base_url: https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic/v1
    api_key: {alibaba_key}
  profile_describer:
    provider: custom
    model: qwen3.6-flash
    base_url: https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic/v1
    api_key: {alibaba_key}
  triage_specifier:
    provider: custom
    model: deepseek-v4-pro
    base_url: https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic/v1
    api_key: {alibaba_key}
mcp_servers:
  gbrain:
    url: http://hermes-gbrain:8083/mcp
    headers:
      Authorization: "Bearer {gbrain_token}"
"""

# Podman secrets used by the stack, mapped to a scratch env name used only for
# the whitespace pre-flight check (so a bad one is reported by secret name).
SECRETS = [
    ("sourcebot-zai-api-key", "SB_ZAI_API_KEY"),
    ("sourcebot-keepa-api-key", "SB_KEEPA_API_KEY"),
    ("sourcebot-logfire-token", "SB_LOGFIRE_TOKEN"),
    ("gbrain-pg-password", "GB_PG_PASSWORD"),
]

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

# Runs inside hermes-opencode (on hermesnet): reads client creds from stdin (or
# registers a new DCR client), mints a client_credentials access token, prints
# {client_id, client_secret, access_token} as JSON (or {"error": ...}).
_GBRAIN_MINT_PY = r'''
import json, sys, urllib.request, urllib.parse
BASE = "http://hermes-gbrain:8083"
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

# --- units --------------------------------------------------------------------
CONTAINER_UNITS = [
    "hermes-opencode",
    "hermes-sidecar",
    "hermes-llama-embed",
    "hermes-llama-rerank",
    "hermes-whisper",
    "hermes-gbrain-pg",
    "hermes-gbrain",
    "hermes-searxng",
    "hermes-trafilatura",
    "hermes-playwright",
    "hermes-sourcebot",
    "hermes-webui",
]
CONFIG_SYNC_UNIT = "hermes-config-sync"
ALL_UNITS = CONTAINER_UNITS + [CONFIG_SYNC_UNIT]

# Readiness gates, checked from the host against the localhost-published ports.
# (name, url or "pg", timeout_s, require_2xx, fatal)
GATES = [
    ("opencode",          "http://127.0.0.1:45650/",                       60,  False, True),
    ("hermes-sidecar",    "http://127.0.0.1:8090/health",                  SIDECAR_READY_TIMEOUT, True, False),
    ("hermes-llama-embed",  "http://127.0.0.1:8084/health",                120, False, False),
    ("hermes-llama-rerank", "http://127.0.0.1:8085/health",                120, False, False),
    ("hermes-whisper", "http://127.0.0.1:8086/health", 120, False, False),
    ("hermes-gbrain-pg",  "pg",                                            30,  False, False),
    ("hermes-gbrain",     "http://127.0.0.1:8083/",                        120, False, False),
    ("hermes-searxng",    "http://127.0.0.1:8888/search?q=test&format=json", 60, False, False),
    ("hermes-trafilatura", "http://127.0.0.1:8100/health",                 30,  False, False),
    ("hermes-playwright", "http://127.0.0.1:8101/health",                  30,  False, False),
    ("hermes-sourcebot",  "http://127.0.0.1:8181/",                        60,  False, False),
    ("hermes-webui",      "http://127.0.0.1:8787/",                        180, False, True),
]


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


def systemctl_user(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return run(["systemctl", "--user", *args], check=check, quiet=True)


def unit_active(unit: str) -> bool:
    r = systemctl_user("is-active", "--quiet", unit, check=False)
    return r.returncode == 0


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


def show_logs(name: str, tail: int = 20) -> None:
    r = run(["podman", "logs", "--tail", str(tail), name], check=False, quiet=True)
    log(((r.stdout or "") + (r.stderr or "")).rstrip())


# --- rendering + installing ---------------------------------------------------
def render_units(cfg: dict[str, str]) -> dict[str, str]:
    subs = {
        "HOME": str(Path.home()),
        "REPO": str(SCRIPT_DIR),
        "PROJECT_MOUNT": PROJECT_MOUNT,
        "GBRAIN_DATA_DIR": str(GBRAIN_DATA_DIR),
        "VEGA_RENDER": render_node(GPU_PCI_VULKAN),
        "ROCR_INDEX": rocm_agent_index(GPU_GFX_ROCM),
        "EMBED_MODEL_FILE": EMBED_MODEL_FILE,
        "RERANK_MODEL_FILE": RERANK_MODEL_FILE,
        "WHISPER_MODEL_FILE": WHISPER_MODEL_FILE,
        "OPENCODE_PASSWORD": cfg["OPENCODE_SERVER_PASSWORD"],
        "HERMES_WEBUI_PASSWORD": cfg["HERMES_WEBUI_PASSWORD"],
        "ZAI_API_KEY": cfg["ZAI_API_KEY"],
        "GBRAIN_ADMIN_TOKEN": cfg["GBRAIN_ADMIN_TOKEN"],
    }
    units: dict[str, str] = {}
    for f in sorted(QUADLET_SRC.iterdir()):
        if f.suffix not in (".container", ".network", ".service"):
            continue
        text = f.read_text()
        for key, val in subs.items():
            text = text.replace("{{" + key + "}}", str(val))
        units[f.name] = text
    return units


def install_units(cfg: dict[str, str]) -> None:
    units = render_units(cfg)
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    SYSTEMD_UNIT_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in units.items():
        # Quadlet scans ~/.config/containers/systemd (its own file types);
        # plain .service units must go to ~/.config/systemd/user instead.
        dest = (SYSTEMD_UNIT_DIR if name.endswith(".service") else UNIT_DIR) / name
        dest.write_text(text)
        dest.chmod(0o600)
        log(f"  installed {dest}")
    systemctl_user("daemon-reload")
    # Sanity: the quadlet generator must have turned each .container into a
    # systemd service, otherwise nothing below can start.
    bad = []
    for name in units:
        if not name.endswith(".container"):
            continue
        svc = name.removesuffix(".container") + ".service"
        if systemctl_user("cat", svc, check=False).returncode != 0:
            bad.append(svc)
    if bad:
        raise RuntimeError(
            f"quadlet generator did not produce: {', '.join(bad)} "
            "(check `journalctl --user -t quadlet-generator`)")
    log(f"  {len(units)} units installed, daemon reloaded")


# --- probes -------------------------------------------------------------------
def http_up(url: str, require_ok: bool = False) -> bool:
    try:
        urllib.request.urlopen(url, timeout=3)
        return True
    except urllib.error.HTTPError:
        return not require_ok   # any HTTP response counts as "up" unless 2xx required
    except Exception:
        return False


def pg_ready() -> bool:
    r = run(["podman", "exec", "hermes-gbrain-pg", "pg_isready", "-U", "gbrain"],
            check=False, quiet=True)
    return r.returncode == 0


def wait_gate(name: str, url: str, timeout: int, require_ok: bool,
              fatal: bool) -> bool:
    deadline = time.time() + timeout
    i = 0
    while time.time() < deadline:
        i += 1
        up = pg_ready() if url == "pg" else http_up(url, require_ok)
        if up:
            log(f"  {name} up after ~{i}s")
            return True
        time.sleep(1)
    msg = f"{name} not responding after {timeout}s"
    if fatal:
        log(f"ERROR: {msg}")
        show_logs(name)
        sys.exit(1)
    log(f"  WARN: {msg} (continuing)")
    return False


# --- secrets ------------------------------------------------------------------
def check_secrets() -> None:
    """Verify podman secrets exist and have no leading/trailing whitespace.

    Secrets are created out-of-band (never stored in .env); a trailing newline
    from `echo` vs `printf '%s'` makes external APIs reject the key.
    """
    args = ["podman", "run", "--rm"]
    mounted: list[tuple[str, str]] = []
    for secret, target in SECRETS:
        if run(["podman", "secret", "exists", secret], check=False, quiet=True).returncode == 0:
            args += ["--secret", f"{secret},type=env,target={target}"]
            mounted.append((secret, target))
        else:
            log(f"WARN: podman secret '{secret}' missing — the container using "
                f"it will fail to start. Create it with: "
                f"printf '%s' \"$VALUE\" | podman secret create {secret} -")
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
        log(f"WARN: secret(s) with leading/trailing whitespace "
            f"(likely `echo` vs `printf '%s'`): {', '.join(bad)}")
        log("      Recreate cleanly: printf '%s' \"$VALUE\" | podman secret create <name> -")


# --- gbrain MCP token + config sync -------------------------------------------
def ensure_gbrain_mcp_token() -> str:
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


def write_hermes_config(gbrain_access: str) -> None:
    """Deep-merge HERMES_CONFIG_YAML into config.yaml on the hermes-data
    volume (host-side write: with keep-id the volume is owned by the host
    user, so no container round-trip is needed)."""
    env = load_env(SCRIPT_DIR / ".env")
    alibaba_key = env.get("ALIBABA_CODING_PLAN_API_KEY") or "MISSING"
    config_yaml = HERMES_CONFIG_YAML.format(
        gbrain_token=gbrain_access or "MISSING",
        alibaba_key=alibaba_key)
    target = HERMES_DATA_VOL / "config.yaml"
    merged_yaml = config_yaml
    if _HAS_YAML and target.exists():
        try:
            existing = yaml.safe_load(target.read_text()) or {}
            merged = yaml.safe_load(config_yaml) or {}

            def deep_merge(dst: dict, src: dict) -> dict:
                for k, v in src.items():
                    if isinstance(v, dict) and isinstance(dst.get(k), dict):
                        deep_merge(dst[k], v)
                    else:
                        dst[k] = v
                return dst

            merged = deep_merge(existing, merged)
            merged_yaml = yaml.safe_dump(merged, default_flow_style=False, sort_keys=False)
        except yaml.YAMLError as e:
            log(f"WARN: could not merge config.yaml ({e}); overwriting")
    target.write_text(merged_yaml)
    log(f"  wrote {target}")


def config_sync(restart_webui: bool = True) -> None:
    """Idempotent config sync, also run as the hermes-config-sync systemd unit.

    Waits for gbrain, mints an MCP token, deep-merges config.yaml. Always
    warn-and-continue: a missing gbrain must never block the webui (the volume
    keeps its last-good config)."""
    log("config-sync: waiting for gbrain ...")
    gbrain_up = False
    for i in range(120):
        if http_up("http://127.0.0.1:8083/"):
            gbrain_up = True
            log(f"  gbrain up after ~{i + 1}s")
            break
        time.sleep(1)

    token = ""
    if gbrain_up:
        try:
            token = ensure_gbrain_mcp_token()
            log("  gbrain MCP access token minted (client_credentials)")
        except RuntimeError as e:
            log(f"WARN: {e}")
    else:
        log("WARN: gbrain not up; keeping last-good config.yaml")

    if token:
        write_hermes_config(token)
        if restart_webui and unit_active("hermes-webui.service"):
            log("  restarting hermes-webui to pick up config ...")
            systemctl_user("restart", "hermes-webui.service")
            wait_gate("hermes-webui", "http://127.0.0.1:8787/", 180, False, False)
    elif gbrain_up:
        log("WARN: no token minted; keeping last-good config.yaml")


# --- models -------------------------------------------------------------------
def download_models() -> None:
    """Download GGUF model files for embedding/reranker if not present."""
    models_dir = GBRAIN_DATA_DIR / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    for name, url in [(EMBED_MODEL_FILE, EMBED_MODEL_URL),
                      (RERANK_MODEL_FILE, RERANK_MODEL_URL),
                      (WHISPER_MODEL_FILE, WHISPER_MODEL_URL)]:
        dest = models_dir / name
        if dest.exists():
            continue
        log(f"  downloading {name} ...")
        r = subprocess.run(["curl", "-L", "--progress-bar", "-o", str(dest), url])
        if r.returncode != 0:
            raise RuntimeError(f"failed to download {name}")


# --- builds -------------------------------------------------------------------
def build_images() -> None:
    log('Building container images ...')
    run(['podman', 'build', '-f', str(SCRIPT_DIR / 'images/opencode/Containerfile'), '-t', 'localhost/hermes-opencode:latest', str(SCRIPT_DIR)])
    run(['podman', 'build', '-f', str(SCRIPT_DIR / 'images/trafilatura/Containerfile'), '-t', 'localhost/hermes-trafilatura:latest', str(SCRIPT_DIR)])
    run(['podman', 'build', '-f', str(SCRIPT_DIR / 'images/playwright/Containerfile'), '-t', 'localhost/hermes-playwright:latest', str(SCRIPT_DIR)])
    run(['podman', 'pull', 'docker.io/searxng/searxng:latest'])
    run(['podman', 'build', '-f', str(SCRIPT_DIR / 'images/webui/Containerfile'), '-t', 'localhost/hermes-webui:latest', str(SCRIPT_DIR)])
    run(['podman', 'build', '-f', str(SCRIPT_DIR / 'images/gbrain/Containerfile'),
         '-t', 'localhost/gbrain:latest', str(SCRIPT_DIR)])
    run(['podman', 'pull', LLAMA_IMAGE_CPU])
    run(['podman', 'pull', LLAMA_IMAGE_VULKAN])
    run(['podman', 'build', '-f', str(SCRIPT_DIR / 'images/whisper/Containerfile'), '-t', 'localhost/whisper-cpp-vulkan:latest', str(SCRIPT_DIR)])


def build_sidecar_image() -> None:
    """Rebuild the ROCm sidecar image (opt-in: heavy base, long compile)."""
    log('Building sidecar image (ROCm 7.2.1 / gfx1201, pinned llama.cpp) ...')
    run(['podman', 'build', '-f', str(SCRIPT_DIR / 'images/sidecar/Containerfile'),
         '-t', LLAMA_IMAGE_ROCM, str(SCRIPT_DIR)])


def recreate_container(name: str) -> None:
    """Stop the unit, drop the container, start fresh (picks up new images)."""
    svc = name + ".service"
    systemctl_user("stop", svc, check=False)
    run(["podman", "rm", "-f", name], check=False, quiet=True)
    systemctl_user("start", svc)


# --- orchestration ------------------------------------------------------------
def load_cfg() -> dict[str, str]:
    cfg = load_env(SCRIPT_DIR / ".env")
    for key in ("OPENCODE_SERVER_PASSWORD", "HERMES_WEBUI_PASSWORD",
                "ZAI_API_KEY", "GBRAIN_ADMIN_TOKEN"):
        if not cfg.get(key):
            log(f"ERROR: {key} must be set in .env")
            sys.exit(1)
    return cfg


def prepare_dirs() -> None:
    (SCRIPT_DIR / "hermes-workspace").mkdir(exist_ok=True)
    for subdir in ("pg", "config", "brain", "models"):
        (GBRAIN_DATA_DIR / subdir).mkdir(parents=True, exist_ok=True)


def start_stack(cfg: dict[str, str]) -> None:
    log("Installing/refreshing quadlet units ...")
    install_units(cfg)
    check_secrets()
    prepare_dirs()
    log("Checking gbrain model files ...")
    download_models()

    # Start everything except the webui; systemd resolves the internal
    # ordering (gbrain after pg). Config sync runs before the webui so the
    # agent boots with a fresh gbrain MCP token + merged config.yaml.
    log("Starting containers ...")
    systemctl_user("start", *[f"{u}.service" for u in CONTAINER_UNITS
                                if u != "hermes-webui"])
    config_sync(restart_webui=False)
    systemctl_user("start", "hermes-webui.service")

    log("Readiness gates ...")
    for name, url, timeout, require_ok, fatal in GATES:
        wait_gate(name, url, timeout, require_ok, fatal)

    banner()


def stop_stack() -> None:
    log("Stopping all units ...")
    systemctl_user("stop", *[f"{u}.service" for u in reversed(ALL_UNITS)],
                   check=False)
    log("Stopped (containers kept; `run.py` starts them again).")


def redeploy(cfg: dict[str, str]) -> None:
    """Stop, remove all containers, start fresh — picks up rebuilt images."""
    systemctl_user("stop", *[f"{u}.service" for u in reversed(ALL_UNITS)],
                   check=False)
    for name in ("hermes-opencode", "hermes-sidecar", "hermes-llama-embed",
                 "hermes-llama-rerank", "hermes-gbrain-pg", "hermes-gbrain",
                 "hermes-searxng", "hermes-trafilatura", "hermes-playwright",
                 "sourcebot", "hermes-webui"):
        run(["podman", "rm", "-f", name], check=False, quiet=True)
    start_stack(cfg)


def status() -> None:
    log("=== systemd units ===")
    r = systemctl_user("list-units", "--all", "--no-pager",
                       "hermes-*.service", "hermesnet-network.service",
                       "sourcebot.service", check=False)
    log((r.stdout or "").rstrip())
    log()
    log("=== containers ===")
    r = run(["podman", "ps", "-a", "--format",
             "table {{.Names}}\t{{.Status}}\t{{.Ports}}"], check=False, quiet=True)
    log((r.stdout or "").rstrip())


def banner() -> None:
    log()
    log("=== Stack started (Quadlet units on hermesnet) ===")
    log("WebUI:       http://127.0.0.1:8787")
    log("Sourcebot:   http://127.0.0.1:8181")
    log("Tailscale:   https://bigbox.kamori-eel.ts.net        (Hermes :443)")
    log("             https://bigbox.kamori-eel.ts.net:8443   (Sourcebot)")
    log("OpenCode:    http://127.0.0.1:45650")
    log("SearXNG:     http://127.0.0.1:8888")
    log("Trafilatura: http://127.0.0.1:8100")
    log("Playwright:  http://127.0.0.1:8101")
    log("gbrain MCP:  http://127.0.0.1:8083/mcp")
    log("Embeddings:  http://127.0.0.1:8084/v1  (Vulkan/Vega 56)")
    log("Reranker:    http://127.0.0.1:8085/v1  (Vulkan/Vega 56)")
    log("Sidecar:     http://127.0.0.1:8090     (ROCm/R9700)")
    log("Manage:      python3 run.py --status | --stop | --redeploy")
    log()


def main() -> None:
    argv = sys.argv[1:]

    if '--build-sidecar' in argv:
        build_sidecar_image()
        log("Recreating hermes-sidecar on the new image ...")
        recreate_container("hermes-sidecar")
        wait_gate("hermes-sidecar", "http://127.0.0.1:8090/health",
                  SIDECAR_READY_TIMEOUT, True, False)
        return
    if '--build' in argv:
        build_images()
        log("Images rebuilt. Run `python3 run.py --redeploy` to pick them up.")
        return

    if '--render' in argv:
        for name, text in render_units(load_cfg()).items():
            log(f"===== {name} =====")
            log(text)
        return

    cfg = load_cfg()

    if '--install' in argv:
        install_units(cfg)
        return
    if '--config-sync' in argv:
        config_sync(restart_webui=True)
        return
    if '--stop' in argv:
        stop_stack()
        return
    if '--status' in argv:
        status()
        return
    if '--redeploy' in argv:
        redeploy(cfg)
        return

    start_stack(cfg)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("\ninterrupted")
        sys.exit(130)
    except RuntimeError as e:
        log(f"ERROR: {e}")
        sys.exit(1)
