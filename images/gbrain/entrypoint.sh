#!/bin/bash
# entrypoint.sh — gbrain container startup
#
# 1. Waits for Postgres
# 2. Writes the effective brain config (local llama.cpp embedding + reranker)
# 3. Runs gbrain schema migrations (schema is sized from embedding_dimensions)
# 4. Starts the gbrain MCP HTTP server (OAuth 2.1; admin bootstrap token via env)
#
# Config note: gbrain treats GBRAIN_HOME as a PARENT dir and appends ".gbrain"
# itself, so the live config file is $GBRAIN_HOME/.gbrain/config.json. With
# GBRAIN_HOME=/root and the host config dir mounted at /root/.gbrain, that file
# is /root/.gbrain/config.json. Embedding model/dims are also forced via the
# GBRAIN_EMBEDDING_MODEL / GBRAIN_EMBEDDING_DIMENSIONS env vars (highest
# precedence in loadConfig); reranker_model has no env override, so it is set
# here in the file plane.

set -e

PG_HOST="${DATABASE_HOST:-localhost}"
PG_PORT="${DATABASE_PORT:-5432}"
PG_USER="${DATABASE_USER:-gbrain}"
PG_DB="${DATABASE_NAME:-gbrain}"
GBRAIN_PORT="${GBRAIN_PORT:-8083}"

EMBED_ALIAS="${EMBED_ALIAS:-Qwen3-Embedding-4B}"
EMBED_DIMENSIONS="${EMBED_DIMENSIONS:-2560}"
RERANK_ALIAS="${RERANK_ALIAS:-Qwen3-Reranker-4B}"
LLAMA_EMBED_URL="${LLAMA_SERVER_BASE_URL:-http://localhost:8084/v1}"
LLAMA_RERANK_URL="${LLAMA_SERVER_RERANKER_BASE_URL:-http://localhost:8085/v1}"

DATABASE_URL="postgresql://${PG_USER}:***@${PG_HOST}:${PG_PORT}/${PG_DB}"
export DATABASE_URL

CONFIG_DIR="${GBRAIN_HOME:-/root}/.gbrain"
CONFIG_FILE="${CONFIG_DIR}/config.json"

echo "[gbrain] Database: postgresql://${PG_USER}:***@${PG_HOST}:${PG_PORT}/${PG_DB}"
echo "[gbrain] Embedding: llama-server:${EMBED_ALIAS} (${EMBED_DIMENSIONS}d) at ${LLAMA_EMBED_URL}"
echo "[gbrain] Reranker:  llama-server-reranker:${RERANK_ALIAS} at ${LLAMA_RERANK_URL}"

# ── Step 1: Wait for Postgres ──────────────────────────────────────────────

echo "[gbrain] Waiting for Postgres at ${PG_HOST}:${PG_PORT}..."
MAX_WAIT=60
WAITED=0
while ! pg_isready -h "$PG_HOST" -p "$PG_PORT" -q; do
    sleep 2
    WAITED=$((WAITED + 2))
    if [ "$WAITED" -ge "$MAX_WAIT" ]; then
        echo "[gbrain] ERROR: Postgres not ready after ${MAX_WAIT}s."
        exit 1
    fi
done
echo "[gbrain] Postgres ready (waited ${WAITED}s)."

# ── Step 2: Write effective config (idempotent) ────────────────────────────
# Points embedding + reranking at the in-pod llama.cpp servers. Written before
# migrations so a fresh schema is sized to EMBED_DIMENSIONS.

mkdir -p "$CONFIG_DIR"
cat > "$CONFIG_FILE" <<EOF
{
    "engine": "postgres",
    "database_url": "${DATABASE_URL}",
    "schema_pack": "gbrain-base-v2",
    "embedding_model": "llama-server:${EMBED_ALIAS}",
    "embedding_dimensions": ${EMBED_DIMENSIONS},
    "reranker_model": "llama-server-reranker:${RERANK_ALIAS}",
    "provider_base_urls": {
        "llama-server": "${LLAMA_EMBED_URL}",
        "llama-server-reranker": "${LLAMA_RERANK_URL}"
    },
    "search": {
        "reranker": { "enabled": true }
    },
    "mcp": {
        "publish_skills": true
    }
}
EOF
echo "[gbrain] Config written to ${CONFIG_FILE}."

# ── Step 3: Apply schema migrations ────────────────────────────────────────

echo "[gbrain] Applying schema migrations..."
gbrain apply-migrations --yes
echo "[gbrain] Migrations complete."

# ── Step 4: Start the MCP HTTP server ──────────────────────────────────────

echo "[gbrain] Starting MCP HTTP server on port ${GBRAIN_PORT}..."
exec gbrain serve --http --port "$GBRAIN_PORT" --host hermes-gbrain --bind 0.0.0.0 --enable-dcr-insecure --token-ttl 31536000
