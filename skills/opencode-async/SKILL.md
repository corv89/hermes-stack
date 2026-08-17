---
name: opencode-async
description: "Async dispatch/collect for the shared OpenCode server — long-running delegated tasks via the HTTP API (scripts included), session-state diagnosis, and oc CLI reality. Companion to opencode-driver."
version: 1.3.0
author: hermes-stack contributors
metadata:
  hermes:
    tags: [OpenCode, Delegation, Async, HTTP-API]
    related_skills: [opencode-driver, sourcebot-ops]
---

# OpenCode Async Delegation

The `opencode-driver` skill covers blocking `oc` calls and prompt/task
discipline — follow it for those. This skill covers what it does NOT
cover: **long-running tasks** (>~9 min) that need fire-and-forget
dispatch plus later collection, because the upstream v2 CLI (`opencode2
run`) and the custom `oc` wrapper are both **blocking-only** — there is
no `--dispatch`, `--wait`, `--status`, or `--collect` flag in either
binary (confirmed 2026-08-17; the upstream v2 line took a different
design path and the v1 releases are a separate product line).

## Installed `oc` surface — blocking only (permanent)

**`oc` (custom Python wrapper at `/usr/local/bin/oc`):**
`oc --help` shows only: `--session, --continue, --model, --provider, --agent,
--timeout, --poll`. **There is no `--dispatch`, `--wait`, `--status`, or
`--collect`** — these flags never shipped in the v2 beta. `ocm` still works
for model/agent switching. Verify with `oc --help` before assuming any flag
exists.

**`opencode2` (upstream v2 CLI, `@opencode-ai/cli@0.0.0-next-15919`):**
`opencode2 run --help` supports: `--session, --continue, --fork, --model,
--agent, --auto, --file, --title, --thinking, --format`. Also **no
`--dispatch`/`--wait`/`--status`/`--collect`**. It is blocking-only
(fire prompt, wait for reply). The upstream v2 CLI took a different
architecture path than the async dispatch mode anticipated in earlier docs.
Requires `XDG_CONFIG_HOME` override to run from non-root (permission issue
on container's `/home/hermeswebui/.config`).

Consequences:
- **Never use foreground `terminal()` for `oc` calls.** The foreground cap
  is 600s and silently truncates longer `--timeout` values. Always use
  `terminal(background=true, notify_on_complete=true)`.
- The server-side session outlives the client wrapper. A timeout exit does
  not mean the agent stopped — reattach with `oc --session <id>` or collect
  via `oc_collect.py`.
- For fire-and-forget dispatch: dispatch + collect via the HTTP API below.
  Sessions persist server-side either way.
- The `opencode2 api` subcommand is a generic HTTP client to the running
  server — useful for ad-hoc API calls but not an async dispatch mode.

## Async pattern (scripts included)

**Dispatch** (exits instantly, session keeps running server-side):

```
terminal(command='python3 <this-skill>/scripts/oc_dispatch.py /workspace/task.md',
         timeout=60)
# stdout: ses_XXXX  (capture this id)
```

`oc_dispatch.py <prompt-file> [--session ses_xxx]` creates the session (or
posts a follow-up turn), sends the prompt, prints the session id.

**Collect** (waits with permission auto-approval + SSE/polling, prints final
reply):

```
terminal(command='python3 <this-skill>/scripts/oc_collect.py ses_XXXX 3600',
         background=true, notify_on_complete=true)
```

## Module-import gotcha

`/usr/local/bin/oc` has no `.py` extension: plain `import oc` fails AND
`importlib.util.spec_from_file_location("oc", ...)` returns a spec with
`loader=None`. The working loader is:

```python
from importlib.machinery import SourceFileLoader
o = SourceFileLoader("oc", "/usr/local/bin/oc").load_module()
```

`oc_collect.py` does this already. Reusable helpers: `o.req`, `o.messages`,
`o.assistant_text`, `o.approve_permissions`, `o.wait_for_completion`.

## Session-state diagnosis

When a waiter "times out", the session may be healthy, done, or zombied.
Check before acting (see `references/http-api.md` for endpoints):

- **Healthy**: latest assistant message `finish=="tool-calls"` with
  advancing `time.completed` timestamps. → keep waiting / re-collect.
- **Done**: `finish` set (non-tool-calls) + `time.completed` + text.
- **ZOMBIE**: `finish=None`, `time.completed` null, no pending permissions
  (`/api/session/{sid}/permission` → `[]`), no questions
  (`/api/session/{sid}/question` → `[]`), and follow-up prompts add no
  messages. Not recoverable — abandon and redispatch a fresh session whose
  prompt is self-contained: bake in everything the stalled session
  discovered (findings, approved decisions) so it doesn't re-block.

Zombies were observed with decision-blocker flows (agent posts a "needs your
call" message and never completes the turn). The fresh-session pattern
resolved it in one shot.

**Also zombie:** Sessions where the agent stopped producing progress
(advancing `time.completed` timestamps or new messages) but never set
`finish`. The agent is not working; it is stuck. Abandon and redispatch.

## Pitfalls

### Timeout philosophy: guard against runaways, not hard-working agents

**The timeout kills the client-side wrapper, not the server-side session.**
When `oc` exits with a timeout code, the agent is usually still running
server-side and doing useful work. A blocking timeout is a client
synchronization failure, not a task failure. Reattach or re-collect
instead of reporting the task as failed.

Timeout exists to protect against **runaway processes** (zombies, infinite
tool loops, hanging providers). It must NOT be used as a task deadline for
legitimate long-running work. A model that spends 15-30 minutes reading
files and reasoning before writing a review is working correctly, not
broken. Killing it at the wrapper level wastes the tokens already burned
and produces zero output.

**Practical rule:** Always delegate via `terminal(background=true,
notify_on_complete=true)` or the HTTP API dispatch/collect pattern. Never
use foreground `terminal()` with a short timeout for anything expected
to take more than a few minutes. The server-side session outlives the
client; let it finish.

### Pipe truncation kills oc output (permanent data loss)

### Pipe truncation kills oc output (permanent data loss)

Piping `oc` output through `head`, `tail`, or any truncation tool destroys
the reply stream irreversibly. The session reports "agent finished but
produced no text" because `head -5` closed the pipe after 5 lines, and `oc`
treated the EPIPE as a signal to stop writing (or the output was simply
truncated before `oc` could flush its response).

**NEVER pipe oc output:** `oc "..." | head -5` or `oc "..." 2>&1 | grep session`.
These patterns produce sessions with zero usable text. The session is
unrecoverable — the text was never stored server-side, only streamed.

**Correct capture patterns:**
```
# Background with output capture — head-5 is NOT in the command
terminal(background=true, command='oc "..." 1>/tmp/oc_out.txt 2>/tmp/oc_err.txt',
         notify_on_complete=true)
# Read results after notification
read_file(path="/tmp/oc_out.txt")
```

If you need only the session id from stderr, capture it separately:
```
terminal(background=true, command='oc "..." 2>/tmp/oc_session.txt',
         notify_on_complete=true)
# Parse session id from stderr later
```

### `--timeout` exceeding terminal foreground cap

Hermes `terminal()` foreground calls are hard-capped at 600s. Passing
`--timeout 900` to `oc` inside a foreground `terminal()` call will silently
cap to 600s — `oc` receives 600, not 900. The `oc` wrapper times out
at 600s, but the server-side session may still be alive.

**Rule:** for any `oc` call needing >600s, use background mode:
```
terminal(background=true, command='oc --timeout 1200 "..."',
         notify_on_complete=true)
```
Background mode has no timeout ceiling on `oc`'s side — the `--timeout`
value reaches the actual `oc` process. Foreground mode caps at 600s before
`oc` even sees the value.

### `--timeout 3` exit code means "timed out, session likely alive"

When `oc` exits with code 3, the session is almost certainly still running
on the server doing useful work. The stderr message tells you the session id
and how to reattach. **Do NOT report failure** — reattach or collect.

### Waiting mechanics gotcha

`terminal`'s `process(action="wait")` is clamped to 180s regardless of the
requested timeout — for oc waits longer than that, rely on
`terminal(background=true, notify_on_complete=true)` (or the collect script
above), not `wait`. Also: a stale/buggy background collector keeps firing
completion notifications after you've replaced it — match the notification's
process/session id against the live one before acting on it.

## Reference

- `references/http-api.md` — endpoint table, auth env vars, session-state
  fields, the helper-reuse snippet, and **provider fallback strategy**
  (`zai-coding-plan` as default for GLM-5.2 — `zai`/`zhipuai` exhaust their
  shared balance; `zai-coding-plan` has separate billing). Other fallbacks:
  `bailian-personal`, `bailian-team`, `opencode` (Zen).
