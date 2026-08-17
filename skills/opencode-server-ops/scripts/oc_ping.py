#!/usr/bin/env python3
"""PONG probe for the OpenCode v2 server — pre-dispatch health check.

Usage:
  python3 oc_ping.py [model_id] [provider_id]
Defaults: glm-5.3 zai-coding-plan (known-good pair, Aug 2026).

Prints VERDICT: EXECUTING or STILL NOT EXECUTING with finish/error detail.
Interrupts its own test session on exit. Exit 0 either way (it's a probe,
not a gate). Use for: (a) the two-provider split test that distinguishes
server-wide wedge from a dead hanging provider; (b) post-restart
verification; (c) pre-dispatch sanity when the last dispatch died.
"""
import base64
import json
import os
import sys
import time
import urllib.request

BASE = os.environ.get("OPENCODE_SERVER_URL", "http://hermes-opencode:45650")
USER = os.environ.get("OPENCODE_SERVER_USER", "")
PASS = (os.environ.get("OPENCODE_SERVER_PASSWORD")
        or os.environ.get("OPENCODE_PASS")
        or os.environ.get("OPENCODE_SERVER_PASS", ""))

MODEL = sys.argv[1] if len(sys.argv) > 1 else "glm-5.3"
PROVIDER = sys.argv[2] if len(sys.argv) > 2 else "zai-coding-plan"


def req(method, path, body=None, timeout=20):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    r.add_header("Content-Type", "application/json")
    tok = base64.b64encode(f"{USER}:{PASS}".encode()).decode()
    r.add_header("Authorization", f"Basic {tok}")
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


sid = None
try:
    sess = req("POST", "/api/session",
               {"agent": "build", "model": {"id": MODEL, "providerID": PROVIDER}})
    sid = (sess.get("data") or {}).get("id")
    if not sid:
        print("VERDICT: CANNOT CREATE SESSION — auth/server down?")
        sys.exit(0)
    req("POST", f"/api/session/{sid}/prompt", {"text": "Reply with exactly: PONG"})
    for i in range(6):  # 60s max
        time.sleep(10)
        msgs = req("GET", f"/api/session/{sid}/message?order=desc&limit=2")
        for m in (msgs.get("data") or []):
            if m.get("type") == "assistant":
                texts = "".join(p.get("text", "") for p in (m.get("content") or [])
                                if p.get("type") == "text")
                if m.get("finish") or texts:
                    err = m.get("error")
                    print(f"[{MODEL}@{PROVIDER}] finish={m.get('finish')} "
                          f"text={texts[:60]!r} err={json.dumps(err)[:160] if err else None}")
                    ok = m.get("finish") == "stop"
                    print("VERDICT:", "EXECUTING" if ok else
                          "RESPONDED WITH ERROR (see above — provider issue, not a wedge)")
                    sys.exit(0)
        # no output yet, keep polling
    print(f"[{MODEL}@{PROVIDER}] no assistant output in 60s — finish=None forever")
    print("VERDICT: STILL NOT EXECUTING")
except Exception as e:
    print(f"PROBE ERROR: {e}", file=sys.stderr)
finally:
    if sid:
        try:
            req("POST", f"/api/session/{sid}/interrupt")
        except Exception:
            pass
