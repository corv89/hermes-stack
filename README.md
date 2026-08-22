# hermes-stack

A self-hosted AI workbench on rootless [Podman] Quadlets. The **Hermes** agent
drives a contained **OpenCode** coding server, backed by a fully-local
**gbrain** RAG memory (Postgres + pgvector + llama.cpp embeddings). The core
loop needs zero external API keys: your models, your memory, your code, all on
your hardware. Cloud providers are wired as escalation, not dependency.

## Why

Most "AI workbench" setups are thin wrappers around cloud APIs. This one is
built backwards from a different premise: the agent, its coding server, its
long-term memory, and its web research tools should all run on hardware you
own, with no vendor lock-in on the critical path. Cloud compute is the fallback
for when the local model is cold or overloaded, not the primary dependency.

The result is a stack where:

- **Nothing leaves your machine** unless you escalate to cloud. Agent state,
  memory, code, and research artifacts all live in local volumes you own.
- **The agent is autonomous.** Hermes delegates coding to OpenCode over HTTP,
  queries gbrain's ~90 MCP tools for recall, and researches the web through
  SearXNG, Trafilatura, and Playwright. It can run multi-step tasks without
  hand-holding.
- **Degradation is graceful.** Only two containers are fatal (the agent + its
  coding server). No GPU, no gbrain, no add-ons? The core pair still runs.
- **Everything is a Quadlet.** No `docker-compose.yml`, no hand-written
  services. Systemd manages the lifecycle; `run.py` renders and drives it.

## Architecture

```
  you --> hermes-webui --> hermes-opencode   (coding server :45650)
          (:8787)              ^
          Hermes Agent         | oc / ocm  (HTTP API)
          + opencode2          v
                    +-------------------------+
                    | gbrain RAG memory       |
                    |  hermes-gbrain  MCP :8083|
                    |  hermes-gbrain-pg        |
                    |  embed  :8084  rerank :8085
                    +-------------------------+
                    +-------------------------+
                    | web-tools               |
                    |  searxng  trafilatura   |
                    |  playwright             |
                    +-------------------------+
                    +-------------------------+
                    | add-ons                 |
                    |  sidecar  primary LLM   |
                    |  whisper  STT           |
                    |  forgejo  git + CI      |
                    |  *-exporter  telemetry  |
                    +-------------------------+
              all on hermesnet bridge network
```

Hermes (the WebUI) is the orchestrator you talk to. It delegates coding tasks
to OpenCode, queries long-term memory through the gbrain MCP server, and
researches the web through a three-stage extraction pipeline. Skills baked into
the WebUI image teach Hermes these flows.

## Quickstart

```bash
cp .env.example .env       # fill in the five required values
python3 run.py --build     # build the images (once)
python3 run.py             # render units, start the stack, run readiness gates
```

`run.py` re-renders and reinstalls units on every run, then starts them.
Quadlet runs containers with `--replace`, so re-runs are safe. Other commands:

```bash
python3 run.py --status         # unit + container overview
python3 run.py --stop           # stop everything (containers kept)
python3 run.py --redeploy       # stop, remove containers, start fresh
python3 run.py --build-sidecar  # rebuild the ROCm sidecar image only
python3 run.py --render         # print rendered units (review/diff)
```

**Boot persistence:** the quadlet generator wires units into `default.target`
on every daemon-reload. With `loginctl enable-linger $USER`, the whole stack
comes up at boot.

## Layers & containers

| Layer | Container | Image | Port | Notes |
|-------|-----------|-------|------|-------|
| Core | `hermes-webui` | built: `images/webui` | 8787 | Hermes Agent + opencode2 CLI; **fatal**; runs keep-id |
| Core | `hermes-opencode` | built: `images/opencode` | 45650 | OpenCode v2 server; **fatal** |
| Memory | `hermes-gbrain` | built: `images/gbrain` | 8083 | gbrain MCP (OAuth 2.1) |
| Memory | `hermes-gbrain-pg` | `pgvector/pgvector:pg17` | - | Postgres + pgvector |
| Memory | `hermes-llama-embed` | built: `images/sidecar` | 8084 | Qwen3-Embedding-4B, ROCm, 2560-d |
| Memory | `hermes-llama-rerank` | built: `images/sidecar` | 8085 | Qwen3-Reranker-4B, ROCm |
| Web | `hermes-searxng` | `searxng/searxng` | 8888 | meta-search |
| Web | `hermes-trafilatura` | built: `images/trafilatura` | 8100 | fast extraction |
| Web | `hermes-playwright` | built: `images/playwright` | 8101 | JS-rendered fallback |
| Add-on | `hermes-sidecar` | built: `images/sidecar` | 8090 | local LLM, Hermes' **primary** model (ROCm) |
| Add-on | `hermes-whisper` | built: `images/whisper` | 8086 | whisper.cpp STT, Vulkan |
| Add-on | `sourcebot` | `sourcebot` * | 8181 | research pipeline (optional, private image) |
| Add-on | `hermes-forgejo` | `forgejo/forgejo:15` | 3000 | git forge + Actions CI |
| Add-on | `hermes-forgejo-runner` | `forgejo/runner:13` | - | Actions runner |
| Add-on | `hermes-codechecker` | `codechecker/codechecker-web:6.28.2` | 8001 | C/C++ static analysis (CodeChecker) |
| Add-on | `hermes-node-exporter` | `prometheus/node-exporter` | 9100 | host telemetry (read-only) |
| Add-on | `hermes-gpu-exporter` | built: `images/gpu-exporter` | 9101 | AMD GPU metrics (read-only) |
| Add-on | `hermes-podman-exporter` | `navidys/prometheus-podman-exporter` | 9102 | container stats (read-only) |

\* The `sourcebot` image is proprietary and optional: built in a separate
private repo. `run.py` detects a missing checkout and skips the unit, its
secrets pre-flight, and its readiness gate. See `.env.example`
(`SOURCEBOT_HOME`).

Images build with the **repo root as context** (`podman build -f
images/<name>/Containerfile .`). `run.py --build` builds everything except the
sidecar; `run.py --build-sidecar` rebuilds the ROCm sidecar (heavy, run only
when bumping llama.cpp or ROCm). Plain `run.py` does **not** rebuild images.

## Repository layout

```
run.py                 orchestrator (render + install + start/status/stop)
quadlet/               Quadlet templates ({{PLACEHOLDER}} substitution)
images/                one dir per built image (webui, opencode, sidecar, ...)
skills/                Hermes agent skills (baked into the webui image)
bin/                   oc, ocm: CLI wrappers baked into the webui image
config/searxng/        settings for the official searxng image
.env / .env.example    secrets (gitignored) / template
docs/                  operational guides (setup, Forgejo, gbrain internals)
```

## Prerequisites

- Linux x86_64, rootless **Podman 5.x** + systemd user units with **linger**
  (`loginctl enable-linger $USER`).
- Python 3.10+ (stdlib only; PyYAML optional for config merging).
- AMD RDNA-family GPU(s). Single-card: sidecar + embed + rerank share one card.
  Dual-card: each component pins its GPU independently via
  `ROCR_VISIBLE_DEVICES`. GPU pinning is overridable via `.env`
  (`GPU_PCI_AUX`, `GPU_PCI_ROCM`, `GPU_DEV_ID_AUX`, `GPU_DEV_ID_SIDECAR`).
  Without GPUs, the GPU-dependent units warn and the rest still runs.
- ~16 GB RAM minimum (core); ~64 GB recommended (full stack).
- SELinux handled via `:z`/`:Z` relabel flags and `SecurityLabelDisable=true`.

## Model topology: local-first, cloud escalation

Hermes runs on the **on-box sidecar by default** (a local LLM served by
llama.cpp on ROCm). A cloud provider is wired as the escalation path:

- **Primary**: `model.provider: custom:sidecar` pointing at the local LLM
  endpoint. Default model `qwen3.6-27b`, context pinned via `model_overrides`.
- **Failover**: `fallback_providers` lists the cloud endpoint. When the primary
  errors (cold start, overload, rate-limit), Hermes walks the chain
  automatically.
- **Manual escalation**: `/model <name> --provider <id>` at runtime;
  `--once` for a single hard turn.

Wiring lives in `HERMES_CONFIG_YAML` in `run.py` (deep-merged into
`config.yaml` by `--config-sync`).

## Secrets

Five keys in `.env` (see `.env.example`): `HERMES_WEBUI_PASSWORD`,
`ZAI_API_KEY`, `OPENCODE_ZHIPU_API_KEY`, `OPENCODE_SERVER_PASSWORD`,
`GBRAIN_ADMIN_TOKEN`. Plus `FORGEJO_ADMIN_PASSWORD` if you enable the forge.

Podman secrets (sourcebot API keys, gbrain Postgres password) are created
out-of-band:

```bash
printf '%s' "$VALUE" | podman secret create <name> -
```

> Use `printf '%s'`, not `echo`. A trailing newline makes external APIs reject
> the key. `run.py` runs a pre-flight check that warns about whitespace.

## Host file access (keep-id)

`hermes-webui` runs with `UserNS=keep-id`: container uid 1000 is the host user.
Everything Hermes writes into the `hermes-data` volume is natively host-owned.
Browse and edit directly, no FUSE/bindfs shim:

```
~/hermes -> ~/.local/share/containers/storage/volumes/hermes-data/_data
~/hermes-workspace -> <repo>/hermes-workspace
```

## Static analysis (CodeChecker)

`hermes-codechecker` runs self-hosted [CodeChecker] as the stack's C/C++
static-analysis leg: clang-tidy + the Clang Static Analyzer over compile
databases, with stored runs and run-to-run diffs in the browser. Python
repos stay on ruff/ty/pytest — this platform is for C/C++ only.

- **Consumers**: casadora-uboot (diff-scoped analysis of the v2026.07 patch
  set) now; ESP32 firmware and out-of-tree kernel modules planned.
- **URL**: http://127.0.0.1:8001, product `Default` (loopback-published for
  the browser; hermesnet-internal for CI containers).
- **Database convention**: two databases on `hermes-gbrain-pg` —
  `codechecker_config` (server config/products) and `default_product` (the
  Default product's runs) — role `codechecker` with trust auth, the same
  boundary as the gbrain Postgres quadlet (5432 is not host-published).
  No `.env` keys, no podman secrets.
- **Host prep**: `/opt/codechecker/workspace` (mode 0700, stack-user owned).
  `run.py` creates it when `/opt/codechecker` permits; otherwise the one-time
  sudo command is in run.py's warning.

`run.py` bootstraps it idempotently (role + databases, unit start, `/ready`
wait, product registration, endpoint check) and warn-and-continue: a
CodeChecker failure never blocks the core stack.

## Operational guides

Detailed setup and operations are in [`docs/`](docs/):

- [Forgejo setup](docs/forgejo.md) - git forge, Actions CI, GitHub mirroring,
  backup/restore, arm64 runner, upgrades
- [gbrain internals](docs/gbrain.md) - RAG memory config, embedding model
  selection, OAuth 2.1 / MCP auth model, schema sizing
- [Telemetry](docs/telemetry.md) - the three Prometheus exporters and their
  read-only bind mounts

## Tailscale (optional)

```bash
sudo tailscale set --operator=$USER          # one-time, enables sudo-free serve
tailscale serve --bg 8787                     # Hermes  -> https://<host>/
tailscale serve --bg --https=8443 8181        # Sourcebot -> https://<host>:8443
```

## Related: CasadoraOS

[CasadoraOS](https://casadora.net) is an image-based Fedora bootc distribution
for home infrastructure built from the same ingredients (Fedora, systemd,
Podman, Quadlets). Running hermes-stack on a CasadoraOS host is a natural
pairing: the stack's quadlets are host-image agnostic. Not yet a supported
configuration, but a promising direction.

## License

[MIT](LICENSE)

[Podman]: https://podman.io
[Forgejo]: https://forgejo.org
[CodeChecker]: https://codechecker.readthedocs.io
