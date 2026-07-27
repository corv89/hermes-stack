#!/bin/bash
# Build all images for the Hermes pod.
# Run this after editing any Containerfile, skill, or wrapper, then `python3 run.py`
# to (re)deploy the pod.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> Building hermes-opencode (opencode v2 server + python/uv tooling)"
podman build -f opencode.Containerfile -t localhost/hermes-opencode:latest .

echo "==> Building hermes-trafilatura (content-extraction API)"
podman build -f web-tools/trafilatura/Containerfile -t localhost/hermes-trafilatura:latest web-tools/trafilatura

echo "==> Building hermes-playwright (JS-rendering extraction API)"
podman build -f web-tools/playwright/Containerfile -t localhost/hermes-playwright:latest web-tools/playwright

echo "==> Pulling searxng (meta-search)"
podman pull docker.io/searxng/searxng:latest

echo "==> Building hermes-webui (webui + agent + oc/ocm + skills)"
podman build -f hermes.Containerfile -t localhost/hermes-webui:latest .

echo "==> All images built. Next: python3 run.py"
