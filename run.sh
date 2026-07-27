#!/bin/bash
# Thin shim — the orchestrator now lives in run.py (Python). Kept so `./run.sh`
# still works; delete freely. The systemd boot unit uses `podman pod start`,
# not this script.
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/run.py" "$@"
