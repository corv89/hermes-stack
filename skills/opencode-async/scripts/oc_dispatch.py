#!/usr/bin/env python3
"""Fire-and-forget dispatch of a task prompt file to the OpenCode server.

The installed `oc` CLI is blocking-only (no --dispatch flag), so async work
drives the same HTTP API oc uses. This script creates a session (or reuses
one), posts the prompt, and prints the session id — then exits immediately.
The session keeps running server-side; collect with oc_collect.py.

Usage:
    python3 oc_dispatch.py /path/to/prompt.md
    python3 oc_dispatch.py /path/to/prompt.md --session ses_xxx   # follow-up turn

Env: OPENCODE_SERVER_URL, OPENCODE_SERVER_USER (default opencode),
OPENCODE_SERVER_PASS (this deployment) — oc itself also reads
OPENCODE_SERVER_PASSWORD / OPENCODE_PASSWORD. OC_AGENT, OC_MODEL_ID,
OC_MODEL_PROVIDER choose agent/model for NEW sessions.
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request

S = os.environ.get("OPENCODE_SERVER_URL", "http://127.0.0.1:45650").rstrip("/")
PW = (
    os.environ.get("OPENCODE_SERVER_PASS")
    or os.environ.get("OPENCODE_SERVER_PASSWORD")
    or os.environ.get("OPENCODE_PASSWORD")
    or ""
)
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
    args = sys.argv[1:]
    sid = None
    if "--session" in args:
        i = args.index("--session")
        sid = args[i + 1]
        del args[i:i + 2]
    if not args:
        print("usage: oc_dispatch.py <prompt-file> [--session SES_ID]", file=sys.stderr)
        sys.exit(2)
    task = open(args[0]).read()

    if sid is None:
        sess = req("POST", "/api/session", {
            "agent": os.environ.get("OC_AGENT", "build"),
            "model": {
                "id": os.environ.get("OC_MODEL_ID", "qwen3.8-max"),
                "providerID": os.environ.get("OC_MODEL_PROVIDER", "bailian-personal"),
            },
        })
        sid = (sess.get("data") or {}).get("id")
        if not sid:
            print("FAILED to create session:", json.dumps(sess)[:300], file=sys.stderr)
            sys.exit(1)

    req("POST", f"/api/session/{sid}/prompt", {"text": task})
    print(sid)


if __name__ == "__main__":
    main()
