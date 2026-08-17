#!/usr/bin/env python3
"""oc_diagnose.py <session-id> — one-shot OpenCode session health check.

Prints: the active-session map, /pending backlog, and the newest turns'
finish/completed/parts health. The zombie-execution signature is
finish=None AND completed=None on the newest assistant turn (often with
empty parts), with any follow-up prompt sitting unprocessed in /pending.

Auth: same env vars as oc (OPENCODE_SERVER_URL, OPENCODE_SERVER_USER,
OPENCODE_PASSWORD | OPENCODE_SERVER_PASS). Run from the skill dir or copy
to /tmp. Validated 2026-08-15 diagnosing a dead session on a
migration-refactor task (died mid-investigation, zero files written).
"""
import base64
import json
import os
import sys
import urllib.request

BASE = os.environ.get("OPENCODE_SERVER_URL", "http://hermes-opencode:45650")
USER = os.environ.get("OPENCODE_SERVER_USER", "")
PASS = os.environ.get("OPENCODE_PASSWORD") or os.environ.get("OPENCODE_SERVER_PASS", "")


def req(method, path):
    r = urllib.request.Request(f"{BASE}{path}", method=method)
    r.add_header(
        "Authorization",
        "Basic " + base64.b64encode(f"{USER}:{PASS}".encode()).decode(),
    )
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]
    except Exception as e:
        return 0, str(e)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sid = sys.argv[1]

    st, active = req("GET", "/api/session/active")
    print("== active sessions (data is a MAP keyed by session id) ==")
    if st == 200 and isinstance(active, dict):
        for s, info in (active.get("data") or {}).items():
            mark = " <== THIS" if s == sid else ""
            print(f"  {s}  {info}{mark}")
    else:
        print(" ", st, active)

    st, pend = req("GET", f"/api/session/{sid}/pending")
    print("\n== pending (unprocessed prompts) ==")
    print(" ", st, json.dumps(pend)[:400])

    st, msgs = req("GET", f"/api/session/{sid}/message?order=desc&limit=6")
    print("\n== newest turns (zombie: finish=None AND completed=None) ==")
    if st == 200 and isinstance(msgs, dict):
        for m in msgs.get("data", []):
            t = m.get("type")
            fin = m.get("finish")
            comp = (m.get("time") or {}).get("completed")
            parts = m.get("content") or []
            kinds = [
                p.get("name") if p.get("type") == "tool" else p.get("type")
                for p in parts
            ]
            print(f"  [{t}] finish={fin} completed={comp} parts={kinds}")
            for p in parts:
                if p.get("type") == "tool":
                    s2 = p.get("state") or {}
                    print(
                        f"     tool={p.get('name')} status={s2.get('status')} "
                        f"input={json.dumps(s2.get('input', {}))[:120]}"
                    )
                elif p.get("type") == "text":
                    print(f"     text: {str(p.get('text', ''))[:200]}")
    else:
        print(" ", st, msgs)


if __name__ == "__main__":
    main()
