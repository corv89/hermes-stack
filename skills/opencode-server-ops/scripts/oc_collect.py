#!/usr/bin/env python3
"""Wait for an OpenCode session to finish and print the final reply.

Usage: oc_collect.py <session-id> [timeout-seconds]   (default timeout 3600)

Approves permission requests every 2s while waiting, then prints the final
assistant text on stdout. Exit codes mirror the `oc` wrapper: 0 done,
1 error/no text.

Implementation note: reuses the installed `oc` wrapper's wait/approve
helpers, loaded via SourceFileLoader — plain `import oc` fails because the
wrapper script has no `.py` extension (and spec_from_file_location returns
a None loader for it). Override the wrapper path with OPENCODE_OC_PATH if
it is not at /usr/local/bin/oc.

Caveat: passes seen=set(), i.e. treats ALL messages as new. Correct for
sessions created by scripts/oc_dispatch.py; for a long-lived session you
continued, fetch existing message ids first if you need to skip them.
"""
import os
import sys
from importlib.machinery import SourceFileLoader

OC_PATH = os.environ.get("OPENCODE_OC_PATH", "/usr/local/bin/oc")
o = SourceFileLoader("oc", OC_PATH).load_module()


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: oc_collect.py <session-id> [timeout-seconds]")
    sid = sys.argv[1]
    timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 3600
    print(f"[collect] waiting for session {sid} (timeout {timeout}s)", file=sys.stderr)
    o.wait_for_completion(sid, set(), timeout)


if __name__ == "__main__":
    main()
