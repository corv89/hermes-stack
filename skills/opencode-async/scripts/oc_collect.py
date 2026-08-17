#!/usr/bin/env python3
"""Wait for an OpenCode session to finish: approves permissions every 2s,
reacts to SSE terminal events (polling fallback), prints the final reply.

Usage:
    python3 oc_collect.py <session_id> [timeout_seconds=3600]

Run via terminal(background=true, notify_on_complete=true) — no ceiling.

Implementation note: /usr/local/bin/oc has no .py extension, so plain
`import oc` and importlib.util.spec_from_file_location both fail;
SourceFileLoader is the working way to reuse oc's HTTP/wait helpers.
"""
import sys
from importlib.machinery import SourceFileLoader

o = SourceFileLoader("oc", "/usr/local/bin/oc").load_module()

sid = sys.argv[1]
timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 3600
print(f"[collect] waiting for session {sid} (timeout {timeout}s)", file=sys.stderr)
o.wait_for_completion(sid, set(), timeout)
