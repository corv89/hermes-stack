# gbrain memory internals

gbrain stores its state under `$GBRAIN_DATA_DIR` (`pg/`, `config/`, `brain/`,
`models/`). The gbrain container is configured entirely by the local llama.cpp
servers, no cloud embedding keys.

## Configuration

- `GBRAIN_HOME=/root` (gbrain appends `.gbrain`, so the live config is the
  mounted `config/config.json`).
- Embedding model/dims are forced via `GBRAIN_EMBEDDING_MODEL` /
  `GBRAIN_EMBEDDING_DIMENSIONS=2560`; the reranker model is written to the
  config file by the entrypoint (it has no env override). Qwen3-Embedding-4B is
  used because pgvector HNSW indexes cap at 4000 dimensions, so the 4096-d 8B
  model cannot be indexed.

## Schema sizing

The Postgres schema is **sized from `embedding_dimensions`**. If you change the
embedding model/dimension on a non-empty brain, re-init the schema; on an empty
brain it is safe to `DROP SCHEMA public CASCADE` and let the entrypoint
re-apply migrations.

## Auth model

The gbrain entrypoint deliberately redacts the password from the connection
URL (`***`) and relies on Postgres **trust** auth. In the pod era gbrain
connected over loopback (the postgres image ships loopback trust); on
hermesnet the same trust boundary is kept with
`POSTGRES_HOST_AUTH_METHOD=trust` (5432 is not host-published). The server
binds `0.0.0.0` (`--bind`) and advertises `hermes-gbrain` as its OAuth issuer
(`--host`).

## MCP endpoint

`http://hermes-gbrain:8083/mcp` on the network, `http://127.0.0.1:8083/mcp`
from the host. Uses OAuth 2.1, so two credentials are involved:

- `GBRAIN_ADMIN_TOKEN` in `.env` (32+ chars, `[A-Za-z0-9_-]+`) is the **admin
  bootstrap token**. `run.py` passes it to the gbrain container, which refuses
  to start with a weak one, and it authenticates the `/admin` UI.
- For Hermes, `run.py` registers a confidential OAuth client via Dynamic
  Client Registration (persisted to `config/.mcp-client.json`) and mints a
  long-lived `client_credentials` **access token** (1-year TTL; gbrain is
  started with `--enable-dcr-insecure --token-ttl 31536000`). That access token
  is written into the Hermes config as a Bearer header, so Hermes can call
  gbrain's ~90 MCP tools directly. It is re-minted by
  `hermes-config-sync.service`, which runs before the webui at boot and on
  every `run.py` start.

## Sidecar model configuration

Two sidecar settings exist because of the 64K gate: Hermes refuses any model
whose context window is below 64K (`MINIMUM_CONTEXT_LENGTH` in `agent_init`),
so the sidecar runs `CTX_SIZE=65536` (a unified KV pool shared by all slots)
with `q8_0` KV quantization (`--cache-type-k/-v`). The sidecar's quantized
weights plus KV cache must fit the card's VRAM alongside the embed/rerank
servers. `context_length` in the config must match the real server pool.
Thinking is ON by default (`REASONING_FORMAT=auto` routes `<think>` blocks to
`reasoning_content`); set `REASONING_FORMAT=none` in the sidecar unit for
faster, reasoning-free replies.
