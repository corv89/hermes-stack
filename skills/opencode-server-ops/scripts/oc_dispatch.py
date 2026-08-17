#!/usr/bin/env python3
"""Fire-and-forget dispatch of a prompt file to a NEW OpenCode session.

Usage: oc_dispatch.py <prompt-file> [model] [provider] [agent]
Prints the new session id on stdout. Requires OPENCODE_SERVER_URL and
OPENCODE_SERVER_PASS (or OPENCODE_PASSWORD) in the environment.

Use this when the installed `oc` wrapper has no --dispatch flag (check
`oc --help` first). POST /api/session/{sid}/prompt is non-blocking: the
agent starts server-side immediately and the client exits, so no terminal
timeout applies. Collect later with scripts/oc_collect.py.
"""
import base64
import json
import os
import sys
import urllib.request

S = os.environ["OPENCODE_SERVER_URL"].rstrip("/")
PW = (os.environ.get("OPENCODE_SERVER_PASS")
      or os.environ.get("OPENCODE_SERVER_PASSWORD")
      or os.environ.get("OPENCODE_PASSWORD") or "")
USER = os.environ.get("OPENCODE_SERVER_USER", "opencode")
AUTH = base64.b64encode(f"{USER}:{PW}".encode()).decode()


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(S + path, data=data, method=method)
    r.add_header("Authorization", f"Basic {AUTH}")
    if data:
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=30) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: oc_dispatch.py <prompt-file> [model] [provider] [agent]")
    task = open(sys.argv[1]).read()
    model = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("OC_MODEL_ID", "qwen3.8-max")
    provider = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("OC_MODEL_PROVIDER", "bailian-personal")
    agent = sys.argv[4] if len(sys.argv) > 4 else os.environ.get("OC_AGENT", "build")

    sess = req("POST", "/api/session", {
        "agent": agent,
        "model": {"id": model, "providerID": provider},
    })
    sid = (sess.get("data") or {}).get("id")
    if not sid:
        sys.exit(f"failed to create session: {json.dumps(sess)[:300]}")

    req("POST", f"/api/session/{sid}/prompt", {"text": task})
    print(sid)


if __name__ == "__main__":
    main()
