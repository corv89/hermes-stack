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
cp .env.example .env       # fill in the eight required values
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

Eight keys in `.env` (see `.env.example`): `HERMES_WEBUI_PASSWORD`,
`ZAI_API_KEY`, `OPENCODE_ZHIPU_API_KEY`, `OPENCODE_SERVER_PASSWORD`,
`GBRAIN_ADMIN_TOKEN`, `HERMES_DASHBOARD_BASIC_AUTH_USERNAME`,
`HERMES_DASHBOARD_BASIC_AUTH_PASSWORD`, `HERMES_DASHBOARD_BASIC_AUTH_SECRET`.
Plus `FORGEJO_ADMIN_PASSWORD` if you enable the forge.

Podman secrets (sourcebot API keys, gbrain Postgres password) are created
out-of-band:

```bash
printf '%s' "$VALUE" | podman secret create <name> -
```

> Use `printf '%s'`, not `echo`. A trailing newline makes external APIs reject
> the key. `run.py` runs a pre-flight check that warns about whitespace.

Optional Buzz platform keys also live in `.env` — see *Buzz (optional Nostr
messaging)*.

## Buzz (optional Nostr messaging)

[Buzz] (buzz.xyz) is Block's open-source Nostr workspace for humans and
agents. Integration path ③ — the native gateway platform — joins Buzz as a
first-class Hermes messaging platform (channels, DMs, mention gating,
threaded replies, cron delivery) while keeping full Hermes memory, skills
and approvals. The platform plugin ships with the installed hermes-agent
(`plugins/platforms/buzz`), and the `buzz` CLI the adapter shells out to is
baked into the webui image at `/usr/local/bin/buzz` (built from a pinned
upstream tag in `images/webui/Containerfile`).

Every key is optional in `.env` — enable by setting both required-to-enable
keys; the default (all unset) leaves the platform off and the gateway
untouched:

| Key | Enable | Purpose |
|-----|--------|---------|
| `BUZZ_RELAY_URL` | yes | community relay URL |
| `BUZZ_PRIVATE_KEY` | yes | agent's Nostr identity (nsec) — the only Buzz secret |
| `BUZZ_HOME_CHANNEL` | no | default channel; also the `deliver=buzz` cron target |
| `BUZZ_CHANNELS` | no | comma-separated channel allowlist |
| `BUZZ_ALLOWED_USERS` | no | npubs that may talk to the agent (private mode) |
| `BUZZ_ALLOW_ALL_USERS` | no | keep `false` — private mode is the default |
| `BUZZ_POLL_INTERVAL` | no | poll transport interval in seconds (default 4) |
| `BUZZ_TRANSPORT` | no | `auto` \| `websocket` \| `poll` |
| `BUZZ_AUTH_TAG` | no | relay auth tag, if the community requires one |
| `BUZZ_CLI_PATH` | no | default: `buzz` on PATH (baked into the image) |
| `BUZZ_CREDENTIALS_FILE` | no | CLI state; point at the hermes-data volume so it survives redeploys |

You generate the nsec — with any Nostr key tool — and join the community
relay as that identity yourself; the pod never generates or rotates keys.
Membership is enforced by the relay, not by Hermes.

**Wiring.** With both required keys set, `run.py` renders the `BUZZ_*` env
into the hermes-webui unit and appends the `gateway.buzz` channel-hygiene
block to `config.yaml`. With either missing, the whole env block is dropped
and no `gateway.buzz` config is written — the gateway starts exactly as
before with the platform simply absent (no crash, no literal `{{...}}`
placeholders in the rendered unit). Unset optional keys are not passed at
all, never as empty strings. Re-run `python3 run.py` after editing `.env`:
rendered units are not live-edited. Env names follow the installed adapter —
`plugins/platforms/buzz/adapter.py` is the source of truth if docs disagree.

**Channel hygiene** — re-applied by every `run.py --config-sync`; edit
`BUZZ_CONFIG_YAML` in `run.py` to change them canonically:

- `require_mention: true` — the agent answers only when mentioned; a
  non-mentioned message is ignored.
- `interim_assistant_messages: false` — no interim assistant chatter.
- `tool_progress: "off"` — no tool-progress noise in the channel.
- `allow_all_users: false` — private mode: only `BUZZ_ALLOWED_USERS` npubs
  get answers.

Cron jobs with `deliver=buzz` fire into `BUZZ_HOME_CHANNEL`. The nsec lives
only in the host `.env` and the user-only rendered unit (mode 600, same
exposure class as `HERMES_WEBUI_PASSWORD`) — never in tracked files, argv
or logs. Do not set `BUZZ_ALLOW_ALL_USERS=true` on a public relay.

Bring-up checklist (on the host):

```bash
python3 run.py --build                                # webui rebuild picks up the buzz CLI stage
podman run --rm localhost/hermes-webui:latest buzz --help          # CLI runs in the fresh image
podman run --rm localhost/hermes-webui:latest \
  ls /usr/local/lib/hermes-agent/plugins/platforms/buzz/           # plugin bundled
$EDITOR .env                                          # set BUZZ_RELAY_URL + BUZZ_PRIVATE_KEY (+ optionals)
python3 run.py                                        # re-render env + config-sync + restart webui
python3 run.py --render | sed -n '/hermes-webui/,/^=====/p'       # eyeball: BUZZ block, no placeholders
podman exec hermes-webui hermes gateway status        # buzz platform listed (absent before)
tail -f ~/hermes/logs/gateway-stdout.log              # joined relay / channels
```

Then mention the agent in `BUZZ_HOME_CHANNEL`: the reply must thread, and a
non-mentioned message must be ignored (`require_mention`). Off-switch check:
comment the two `.env` keys, re-run `python3 run.py`, and
`hermes gateway status` shows no buzz and no errors.

[Buzz]: https://buzz.xyz

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
tailscale serve --bg --https=9443 9119        # Dashboard (Desktop Remote GW) -> https://<host>:9443
```

### Hermes Desktop (Remote Gateway)

The dashboard backend in the webui container (`:9119`, basic-auth gated) is
what the Hermes Desktop app attaches to via Settings → Gateways → Remote
gateway. Point it at `https://<host>:9443` (or `http://<tailscale-ip>:9119`)
and sign in with the `HERMES_DASHBOARD_BASIC_AUTH_*` values from `.env`.
Health check: `curl -s https://<host>:9443/api/status` → `auth_required: true`,
providers `["basic"]`.

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
