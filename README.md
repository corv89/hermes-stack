# hermes-pod

A self-hosted AI workbench in a single rootless [Podman] pod: the **Hermes**
agent drives a contained **OpenCode** coding server, backed by a fully-local
**gbrain** RAG memory (Postgres + pgvector + llama.cpp embeddings/reranking) and
a small **web-tools** stack for research and extraction.

The whole stack is declared and orchestrated by one dependency-aware Python
script, [`run.py`](run.py) — no compose file, no external services required for
the core.

## Architecture

```
                        ┌────────────────────────────────────────────┐
                        │                pod: hermes                 │
                        │                                            │
  you ──► hermes-webui ─┼─► hermes-opencode   (coding server :45650) │
          (:8787)       │        ▲                                   │
          Hermes Agent  │        │ oc / ocm  (HTTP API)              │
          + opencode2   │        │                                   │
                        │   ┌────┴─────────────────────────────┐     │
                        │   │ gbrain RAG memory                │     │
                        │   │  hermes-gbrain      MCP :8083    │     │
                        │   │  hermes-gbrain-pg   pgvector     │     │
                        │   │  hermes-llama-embed :8084 (CPU)  │     │
                        │   │  hermes-llama-rerank:8085 (GPU)  │     │
                        │   └──────────────────────────────────┘     │
                        │   ┌──────────────────────────────────┐     │
                        │   │ web-tools (research + extract)   │     │
                        │   │  hermes-searxng      :8080       │     │
                        │   │  hermes-trafilatura  :8000       │     │
                        │   │  hermes-playwright   :8001       │     │
                        │   └──────────────────────────────────┘     │
                        │   ┌──────────────────────────────────┐     │
                        │   │ add-ons (images built elsewhere) │     │
                        │   │  sidecar    local 27B LLM :8090  │     │
                        │   │  sourcebot  research bot  :8181  │     │
                        │   └──────────────────────────────────┘     │
                        └────────────────────────────────────────────┘
```

Hermes (the WebUI) is the orchestrator you talk to. It delegates coding tasks to
the OpenCode server over its HTTP API (via the baked-in `oc` / `ocm` wrappers),
queries long-term memory through the gbrain MCP server, and researches the web
through SearXNG → Trafilatura → Playwright. The two skills under [`skills/`](skills/)
(`opencode-driver`, `web-research`) teach Hermes these flows and are baked into
the WebUI image.

## Layers & containers

| Layer | Container | Image | Port (host) | Notes |
|-------|-----------|-------|-------------|-------|
| Core | `hermes-webui` | built: `images/webui` | 8787 | Hermes Agent + opencode2 CLI; **fatal** |
| Core | `hermes-opencode` | built: `images/opencode` | 45650 (internal) | OpenCode v2 server; **fatal** |
| Memory | `hermes-gbrain` | built: `images/gbrain` | 8083 (internal) | gbrain MCP (OAuth 2.1) |
| Memory | `hermes-gbrain-pg` | `pgvector/pgvector:pg17` | 5432 (internal) | Postgres + pgvector |
| Memory | `hermes-llama-embed` | `llama.cpp:server` | 8084 | Qwen3-Embedding-4B, CPU, 2560-d |
| Memory | `hermes-llama-rerank` | `llamacpp-sidecar` | 8085 | Qwen3-Reranker-4B, GPU |
| Web | `hermes-searxng` | `searxng/searxng` | 8888→8080 | meta-search |
| Web | `hermes-trafilatura` | built: `images/trafilatura` | 8100→8000 | fast extraction |
| Web | `hermes-playwright` | built: `images/playwright` | 8101→8001 | JS-rendered fallback |
| Add-on | `sidecar` | `llamacpp-sidecar` * | 8090 (internal) | local 27B LLM |
| Add-on | `sourcebot` | `sourcebot` * | 8181 | autonomous research pipeline |

\* The `sidecar` and `sourcebot` images are **built in separate repos**
(`llama.cpp` and `sourcebot` respectively) and are optional. `run.py` pulls /
builds everything else.

**Graceful degradation:** only `hermes-opencode` and `hermes-webui` are fatal.
Every other container is warn-and-continue, and gbrain is skipped if Postgres or
the embedding server isn't ready. So the core agent pair runs even on a machine
with no GPU, no gbrain, and no add-ons.

## Repository layout

```
run.py                 orchestrator (entry point)
images/                one dir per image we build (bare "Containerfile")
  webui/  opencode/  trafilatura/  playwright/  gbrain/
skills/                Hermes agent skills (baked into the webui image)
  opencode-driver/  web-research/
bin/                   oc, ocm — CLI wrappers baked into the webui image
config/searxng/        settings.yml for the official searxng image
.env / .env.example    secrets (gitignored) / template
```

All images build with the **repo root as the build context**
(`podman build -f images/<name>/Containerfile .`).

## Prerequisites

- Linux x86_64 with rootless **Podman** (developed on Fedora, podman 5.x).
- Python 3.10+ (stdlib only; `PyYAML` optional, used to *merge* rather than
  overwrite the Hermes config).
- An **AMD ROCm GPU** for the reranker and sidecar (developed on gfx1201/RDNA4).
  Without one, those two containers simply warn and the rest still runs.
- ~16 GB RAM minimum for the core; ~64 GB recommended with the full stack.
- SELinux: handled via `:z`/`:Z` relabel flags and `--security-opt label=disable`
  where a container must read host model files.

## Quickstart

```bash
cp .env.example .env       # then fill in the four required values
python3 run.py             # builds images, downloads models, starts the pod
```

`run.py` is re-runnable: it tears down and recreates the pod, but named volumes
and bind mounts persist, so no data is lost. Use `python3 run.py --no-build` to
skip image rebuilds.

On first run it downloads the embedding/reranker GGUF models into
`$GBRAIN_DATA_DIR/models` (default `/opt/gbrain-data`).

### Secrets

Four keys live in `.env` (see `.env.example`). In addition, `sourcebot` and the
gbrain Postgres password use **podman secrets** created out-of-band:

```bash
printf '%s' "$VALUE" | podman secret create <name> -
```

> Use `printf '%s'`, **not** `echo` — `echo` appends a trailing newline, which
> makes external APIs (e.g. Keepa) reject the key while it looks empty/expired.
> `run.py` runs a pre-flight check that warns about whitespace in known secrets.

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

The gbrain MCP endpoint (`http://localhost:8083/mcp`, pod-internal) uses OAuth
2.1, so two credentials are involved:

- `GBRAIN_ADMIN_TOKEN` in `.env` (≥32 chars, `[A-Za-z0-9_-]+`) is the **admin
  bootstrap token**. `run.py` passes it to the gbrain container — which refuses
  to start with a weak one — and it authenticates the `/admin` UI.
- For Hermes, `run.py` registers a confidential OAuth client via Dynamic Client
  Registration (persisted to `config/.mcp-client.json`) and mints a long-lived
  `client_credentials` **access token** (1-year TTL; gbrain is started with
  `--enable-dcr-insecure --token-ttl 31536000`). That access token is written
  into the Hermes config as a Bearer header, so Hermes can call gbrain's ~90
  MCP tools directly. It is re-minted on every `run.py` run.

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
