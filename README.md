# hermes-pod

A self-hosted AI workbench on rootless [Podman] **Quadlet units**: the
**Hermes** agent drives a contained **OpenCode** coding server, backed by a
fully-local **gbrain** RAG memory (Postgres + pgvector + llama.cpp
embeddings/reranking) and a small **web-tools** stack for research and
extraction.

Every container is declared as a Quadlet template under [`quadlet/`](quadlet/);
[`run.py`](run.py) renders them (substituting GPU devices and secrets),
installs them into `~/.config/containers/systemd/` (plain units into
`~/.config/systemd/user/`) and drives the stack through systemd user units.
All containers join one bridge network, **hermesnet**, and reach each other by
container name (`hermes-opencode`, `hermes-gbrain`, `hermes-sidecar`, ...).

## Architecture

```
                    ┌────────────── network: hermesnet ──────────────┐
                    │                                                │
  you ──► hermes-webui ──► hermes-opencode   (coding server :45650)  │
          (:8787)              ▲                                     │
          Hermes Agent         │ oc / ocm  (HTTP API)                │
          + opencode2          │                                     │
                    │   ┌──────┴───────────────────────────────┐     │
                    │   │ gbrain RAG memory                    │     │
                    │   │  hermes-gbrain       MCP :8083       │     │
                    │   │  hermes-gbrain-pg    pgvector        │     │
                    │   │  hermes-llama-embed  :8084 (Vega)    │     │
                    │   │  hermes-llama-rerank :8085 (Vega)    │     │
                    │   └──────────────────────────────────────┘     │
                    │   ┌──────────────────────────────────────┐     │
                    │   │ web-tools (research + extract)       │     │
                    │   │  hermes-searxng      :8080           │     │
                    │   │  hermes-trafilatura  :8000           │     │
                    │   │  hermes-playwright   :8001           │     │
                    │   └──────────────────────────────────────┘     │
                    │   ┌──────────────────────────────────────┐     │
                    │   │ add-ons                              │     │
                    │   │  hermes-sidecar  primary LLM   :8090 │     │
                    │   │  sourcebot       research bot  :8181 │     │
                    │   │  hermes-forgejo  git forge     :3000 │     │
                    │   │  hermes-forgejo-runner Actions runner│     │
                    │   └──────────────────────────────────────┘     │
                    └────────────────────────────────────────────────┘
```

Hermes (the WebUI) is the orchestrator you talk to. It delegates coding tasks
to the OpenCode server over its HTTP API (via the baked-in `oc` / `ocm`
wrappers), queries long-term memory through the gbrain MCP server, and
researches the web through SearXNG → Trafilatura → Playwright. The two skills
under [`skills/`](skills/) (`opencode-driver`, `web-research`) teach Hermes
these flows and are baked into the WebUI image.

## Model topology — local-first, cloud escalation

Hermes runs its agent loop on the **on-box sidecar by default** and keeps a
cloud provider wired as the escalation path. The wiring lives in
`HERMES_CONFIG_YAML` in `run.py` (deep-merged into `config.yaml` by
`--config-sync`):

- **Primary** — `model.provider: custom:sidecar`, a named custom provider
  pointing at `http://hermes-sidecar:8090/v1`. Default model `qwen3.6-27b`
  (the sidecar's `--alias`), `context_length: 65536`.
- **Failover** — `fallback_providers` lists the cloud `zai` / `glm-5-turbo`
  endpoint. When the primary errors (sidecar down, overloaded, rate-limit,
  connection), Hermes walks the chain automatically; `agent.api_max_retries: 1`
  makes failover fast. This also covers the sidecar's cold start while the 27B
  weights are still loading.
- **Manual escalation** — at runtime via the gateway slash command
  `/model glm-5-turbo --provider zai` (append `--once` for a single hard
  turn), back with `/model qwen3.6-27b --provider custom:sidecar`.

Two sidecar settings exist because of the 64K gate: Hermes refuses any model
whose context window is below 64K (`MINIMUM_CONTEXT_LENGTH` in `agent_init`),
so the sidecar runs `CTX_SIZE=65536` (a unified KV pool shared by all slots)
with `q8_0` KV quantization (`--cache-type-k/-v`) to keep that pool inside the
R9700's VRAM beside the ~18GB weights. `context_length` in the config must
match the real server pool. Thinking is ON by default (`REASONING_FORMAT=auto`
routes `<think>` blocks to `reasoning_content`); set `REASONING_FORMAT=none` in
the sidecar unit for faster, reasoning-free replies.

## Layers & containers

| Layer | Container (unit) | Image | Port (host) | Notes |
|-------|------------------|-------|-------------|-------|
| Core | `hermes-webui` | built: `images/webui` | 8787 | Hermes Agent + opencode2 CLI; **fatal**; runs keep-id |
| Core | `hermes-opencode` | built: `images/opencode` | 45650 | OpenCode v2 server; **fatal** |
| Memory | `hermes-gbrain` | built: `images/gbrain` | 8083 | gbrain MCP (OAuth 2.1) |
| Memory | `hermes-gbrain-pg` | `pgvector/pgvector:pg17` | (not published) | Postgres + pgvector |
| Memory | `hermes-llama-embed` | `llama.cpp:server-vulkan` | 8084 | Qwen3-Embedding-4B, Vulkan on Vega 56, 2560-d |
| Memory | `hermes-llama-rerank` | `llama.cpp:server-vulkan` | 8085 | Qwen3-Reranker-4B, Vulkan on Vega 56 |
| Web | `hermes-searxng` | `searxng/searxng` | 8888→8080 | meta-search |
| Web | `hermes-trafilatura` | built: `images/trafilatura` | 8100→8000 | fast extraction |
| Web | `hermes-playwright` | built: `images/playwright` | 8101→8001 | JS-rendered fallback |
| Add-on | `hermes-sidecar` | built: `images/sidecar` | 8090 | local 27B LLM — Hermes' **primary** model (ROCm on R9700) |
| Add-on | `sourcebot` | `sourcebot` * | 8181 | autonomous research pipeline |
| Add-on | `hermes-forgejo` | `forgejo/forgejo:15` | 3000 | git forge: repos, PRs, Actions |
| Add-on | `hermes-forgejo-runner` | `forgejo/runner:13` | — | Actions runner via host podman socket |

\* The `sourcebot` image is **built in its own repo** and is optional. The
`sidecar` image builds from `images/sidecar/Containerfile` via
`python3 run.py --build-sidecar` (pinned llama.cpp commit; heavy — run only
when bumping llama.cpp or ROCm). `python3 run.py --build` builds everything
else; plain `python3 run.py` does **not** rebuild images.

**Graceful degradation:** only `hermes-opencode` and `hermes-webui` are fatal
readiness gates. Everything else is warn-and-continue, and gbrain tolerates a
missing Postgres/embedding server (it keeps the last-good config). So the core
agent pair runs even on a machine with no GPU, no gbrain, and no add-ons.

## Repository layout

```
run.py                 orchestrator (render + install + start/status/stop)
quadlet/               Quadlet templates ({{PLACEHOLDER}} substitution)
images/                one dir per image we build (bare "Containerfile")
  webui/  opencode/  trafilatura/  playwright/  gbrain/  sidecar/
skills/                Hermes agent skills (baked into the webui image)
  opencode-driver/  web-research/
bin/                   oc, ocm — CLI wrappers baked into the webui image
config/searxng/        settings.yml for the official searxng image
.env / .env.example    secrets (gitignored) / template
```

All images build with the **repo root as the build context**
(`podman build -f images/<name>/Containerfile .`).

## Prerequisites

- Linux x86_64 with rootless **Podman** (developed on Fedora, podman 5.8+;
  the Quadlet keys used here need the 5.x generator) and systemd user units
  with **linger** enabled (`loginctl enable-linger $USER`) for boot persistence.
- Python 3.10+ (stdlib only; `PyYAML` optional, used to *merge* rather than
  overwrite the Hermes config).
- GPUs — developed on a dual-AMD setup:
  - **Vega 56 (gfx900)** — embeddings + reranker via the llama.cpp **Vulkan**
    backend (`ghcr.io/ggml-org/llama.cpp:server-vulkan`). ROCm dropped gfx900,
    but Vulkan (RADV) still supports it. Render node resolved via
    `/dev/dri/by-path` at render time.
  - **R9700 (gfx1201)** — the 27B sidecar via **ROCm**, pinned with
    `ROCR_VISIBLE_DEVICES` (resolved from the KFD topology at render time): a
    ROCm runtime supports only one GPU generation, and llama.cpp's HIP init
    fails when it also sees the Vega. The sidecar container needs **full
    `/dev/dri`** (render-node-only breaks ROCm's agent/node mapping).
  Without GPUs these containers warn and the rest of the stack still runs.
- ~16 GB RAM minimum for the core; ~64 GB recommended with the full stack.
- SELinux: handled via `:z`/`:Z` relabel flags and `SecurityLabelDisable=true`
  where a container must read host model files.

## Quickstart

```bash
cp .env.example .env       # then fill in the five required values
python3 run.py --build     # once: build the images
python3 run.py             # install/refresh units, start the stack, run gates
```

`run.py` re-renders and reinstalls the units on every run, then starts them
(quadlet runs containers with `--replace`, so re-runs are safe; named volumes
and bind mounts persist). Other commands:

```bash
python3 run.py --status         # unit + container overview
python3 run.py --stop           # stop everything (containers kept)
python3 run.py --config-sync    # re-mint gbrain token + merge config.yaml
python3 run.py --redeploy       # stop, remove containers, start fresh
python3 run.py --build-sidecar  # rebuild + recreate only the sidecar
python3 run.py --render         # print rendered units (review/diff)
```

On first run it downloads the embedding/reranker GGUF models into
`$GBRAIN_DATA_DIR/models` (default `/opt/gbrain-data`).

**Boot persistence:** the quadlet generator wires the units into
`default.target` on every daemon-reload, so with linger enabled the whole
stack comes up at boot — no hand-written service.

### Secrets

Five keys live in `.env` (see `.env.example`): the four core keys plus
`FORGEJO_ADMIN_PASSWORD` (the Forgejo admin account `run.py` bootstraps). In
addition, `sourcebot` and the gbrain Postgres password use **podman secrets**
created out-of-band:

```bash
printf '%s' "$VALUE" | podman secret create <name> -
```

> Use `printf '%s'`, **not** `echo` — `echo` appends a trailing newline, which
> makes external APIs (e.g. Keepa) reject the key while it looks empty/expired.
> `run.py` runs a pre-flight check that warns about whitespace in known secrets.

The Forgejo **runner registration secret** is deliberately *not* in `.env`:
`run.py` auto-generates it and persists it in
`/opt/forgejo-data/.runner-creds.json` (mode `0600`).

### gbrain memory

gbrain stores its state under `$GBRAIN_DATA_DIR` (`pg/`, `config/`, `brain/`,
`models/`). The gbrain container is configured entirely by the local llama.cpp
servers — no cloud embedding keys:

- `GBRAIN_HOME=/root` (gbrain appends `.gbrain`, so the live config is the
  mounted `config/config.json`).
- Embedding model/dims are forced via `GBRAIN_EMBEDDING_MODEL` /
  `GBRAIN_EMBEDDING_DIMENSIONS=2560`; the reranker model is written to the config
  file by the entrypoint (it has no env override). Qwen3-Embedding-4B is used
  because pgvector HNSW indexes cap at 4000 dimensions, so the 4096-d 8B model
  cannot be indexed.
- The Postgres schema is **sized from `embedding_dimensions`**. If you change the
  embedding model/dimension on a non-empty brain, re-init the schema; on an empty
  brain it is safe to `DROP SCHEMA public CASCADE` and let the entrypoint
  re-apply migrations.
- **Auth model:** the gbrain entrypoint deliberately redacts the password from
  the connection URL (`***`) and relies on Postgres **trust** auth — in the pod
  era gbrain connected over loopback (the postgres image ships loopback trust);
  on hermesnet the same trust boundary is kept with
  `POSTGRES_HOST_AUTH_METHOD=trust` (5432 is not host-published). The server
  binds `0.0.0.0` (`--bind`) and advertises `hermes-gbrain` as its OAuth
  issuer (`--host`).
- **MCP endpoint** (`http://hermes-gbrain:8083/mcp` on the network,
  `http://127.0.0.1:8083/mcp` from the host) uses OAuth 2.1, so two
  credentials are involved:
  - `GBRAIN_ADMIN_TOKEN` in `.env` (≥32 chars, `[A-Za-z0-9_-]+`) is the
    **admin bootstrap token**. `run.py` passes it to the gbrain container —
    which refuses to start with a weak one — and it authenticates the `/admin`
    UI.
  - For Hermes, `run.py` registers a confidential OAuth client via Dynamic
    Client Registration (persisted to `config/.mcp-client.json`) and mints a
    long-lived `client_credentials` **access token** (1-year TTL; gbrain is
    started with `--enable-dcr-insecure --token-ttl 31536000`). That access
    token is written into the Hermes config as a Bearer header, so Hermes can
    call gbrain's ~90 MCP tools directly. It is re-minted by
    `hermes-config-sync.service`, which runs before the webui at boot and on
    every `run.py` start.

## Host file access (keep-id — no FUSE shim)

`hermes-webui` runs with `UserNS=keep-id:uid=1000,gid=1000` + `User=1000:1000`
(see `quadlet/hermes-webui.container`): container uid 1000 **is** the host
user, so everything Hermes writes into the `hermes-data` volume is natively
owned by you — browse and edit it directly, no bindfs/remapping needed:

```
~/hermes -> ~/.local/share/containers/storage/volumes/hermes-data/_data
~/hermes-workspace -> <repo>/hermes-workspace
```

The image entrypoint detects the non-root start, verifies
`WANTED_UID`/`WANTED_GID` match, seeds `/app` from `/apptoo` (patched to
`cp -r`: `cp -a` fails preserving attributes on the root-owned `/app` when
unprivileged) and runs the server directly. The agent's dot-dirs that live
outside the volume get their own named volumes
(`hermes-webui-local:/home/hermeswebui/.local`,
`hermes-webui-cache:/home/hermeswebui/.cache`) because the image-owned
`/home/hermeswebui` cannot be chowned under keep-id.

Everything else (opencode, gbrain, sourcebot, ...) keeps the default rootless
user namespace — their volumes stay subordinate-uid owned and are managed
through the containers, exactly as before.

## Forgejo (git forge)

Self-hosted [Forgejo] (v15 LTS) for repos, PRs, and Forgejo Actions CI with a
local runner on BigBox. GitHub stays as a **push-mirror backup** (configured
per-repo after install — not automated). The web installer is disabled
(`INSTALL_LOCK`), so `run.py` bootstraps the admin account and registers the
Actions runner automatically (`forgejo_bootstrap`, idempotent,
warn-and-continue like every add-on).

**One-time host prep** — data dirs owned by you, plus the rootless podman
socket the runner drives jobs through (Docker-compatible API):

```bash
sudo mkdir -p /opt/forgejo-data /opt/forgejo-runner/config /opt/forgejo-runner/data
sudo chown "$USER": /opt/forgejo-data /opt/forgejo-runner /opt/forgejo-runner/config /opt/forgejo-runner/data
systemctl --user enable --now podman.socket   # runner's Docker-compatible endpoint
```

**`.env` keys** — `FORGEJO_ADMIN_USER`, `FORGEJO_ADMIN_EMAIL`,
`FORGEJO_ADMIN_PASSWORD` are required (the web installer is off, so the admin
account can only come from `run.py`). Optional `FORGEJO_ROOT_URL` for public
links/emails once exposed via Tailscale.

**Access** — http://127.0.0.1:3000. Tailnet exposure: `tailscale serve --bg 3000`,
then set `FORGEJO_ROOT_URL` to the https URL and re-run `python3 run.py`.

**GitHub backup mirror** (per repo, one-time) — create the repo on GitHub,
create a PAT (classic, `repo` scope), then in Forgejo: Settings → Repository →
Mirror Settings → push mirror `https://github.com/<user>/<repo>.git`,
username + PAT, enable "Sync when new commits are pushed". API equivalent:
`POST /api/v1/repos/{owner}/{repo}/push_mirrors`.

> **Warning:** the push mirror **force-pushes**. GitHub is the backup, never
> the primary — once mirroring is on, never push to GitHub directly, or a
> mirror sync will clobber divergent GitHub-side commits.

**Migrating a repo from GitHub** (make Forgejo the primary-of-record) —
`git clone --mirror` on the host, push the mirror to Forgejo, then add the
push mirror back to GitHub, then flip your local clones' `origin` to Forgejo.

**Porting workflows** — copy `.github/workflows/` → `.forgejo/workflows/`.
Forgejo Actions is familiar, not byte-compatible (runner v13 is stricter: no
`set-output`/`add-path`, invalid matrices fail hard). `DEFAULT_ACTIONS_URL`
points at GitHub, so ported workflows keep using `uses: actions/checkout@v4`
unchanged.

**Backup/restore** — git data is covered by the GitHub mirror; for a full
instance snapshot:

```bash
podman exec hermes-forgejo forgejo dump -f /data/forgejo-dump.zip && \
  podman cp hermes-forgejo:/data/forgejo-dump.zip .
```

**Adding the arm64 runner later** (CM5 or another tailnet box) — install the
`forgejo-runner` binary (or the same container image) there, register it
offline from BigBox with
`forgejo forgejo-cli actions register --name <n> --scope '' --secret <40hex>`,
and point its config at the tailnet URL of the forge.

**Upgrades** — bump the image tag. Patch-level `:15` tag bumps are safe:
`podman pull` + `python3 run.py --redeploy`. Major upgrades (X → X+1) require
reading the release notes for manual steps first.

## Tailscale (optional)

To reach the WebUI and Sourcebot from your tailnet only:

```bash
sudo tailscale set --operator=$USER          # one-time, enables sudo-free serve
tailscale serve --bg 8787                     # Hermes  -> https://<host>/
tailscale serve --bg --https=8443 8181        # Sourcebot -> https://<host>:8443
```

## License

[MIT](LICENSE)

[Podman]: https://podman.io
[Forgejo]: https://forgejo.org
