---
name: opencode-server-ops
description: "Operational diagnostics for the OpenCode v2 server — model registration pitfalls, SSE event taxonomy, timeout debugging, zombie session cleanup, server API endpoints. Use alongside opencode-driver when debugging OpenCode timeouts, model issues, or the oc wait mechanism."
version: 2.3.0
author: hermes-stack contributors
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [OpenCode, Diagnostics, API, Operations]
    related_skills: [opencode-driver]
---

# OpenCode Server Operations & Diagnostics

This skill captures operational knowledge for the OpenCode v2 server that goes
beyond the `oc`/`ocm` CLI interface documented in `opencode-driver`. Use it when
debugging timeouts, model issues, zombie sessions, or planning improvements to
the `oc` wait mechanism.

**Relationship to `opencode-driver`:** That skill covers *how to use* `oc`/`ocm`.
This skill covers *how to diagnose and improve* the server and its CLI wrappers.
They overlap in the model-selection territory; see the note at the end.

**No `--dispatch`/`--wait`/`--status`/`--collect` — confirmed permanent
(checked 2026-08-17).** Earlier docs anticipated these as an "oc v4.0.0+
feature"; the upstream v2 CLI (`opencode2 run`) took a different design
path (blocking one-shot, no async subcommand) and the v1 releases
(`v1.18.x` on GitHub) are a separate product line. The custom `oc` wrapper
at `/usr/local/bin/oc` and the `opencode2 run` binary are both
blocking-only. A blocking timeout (exit 1: `oc: timed out waiting for
OpenCode to finish`) means the **session is still running server-side** —
not that the task failed. Reattach via `oc --session <id>` or collect via
`oc_collect.py`.

**Async pattern for long tasks:** two approaches work:
(a) `terminal(background=true, notify_on_complete=true)` wrapping a
blocking `oc --timeout <seconds>` — the delegation outlives Hermes'
foreground terminal limit (exit 124); (b) true fire-and-forget via the
HTTP API (`POST /api/session/{sid}/prompt` is non-blocking; session keeps
running server-side after client exits) — see next subsection.

### Async dispatch without `oc` async flags (validated 2026-08-11)

The key fact: `POST /api/session/{sid}/prompt` is **non-blocking** — it
queues the prompt and returns immediately while the agent runs server-side.
So fire-and-forget dispatch works with two plain API calls even on a
blocking-only `oc` build (the session keeps running after your client exits;
verified across process death). Auth uses the same env vars as `oc`
(`OPENCODE_SERVER_URL`, `OPENCODE_SERVER_USER`, `OPENCODE_SERVER_PASS`);
all endpoints live under the `/api` prefix.

```python
# dispatch: create session + send prompt, exit immediately
sess = req('POST', '/api/session', {'agent': 'build',
    'model': {'id': 'qwen3.8-max', 'providerID': 'bailian-personal'}})
sid = (sess.get('data') or {}).get('id')
req('POST', f'/api/session/{sid}/prompt', {'text': open(prompt_file).read()})
```

Ready-to-run versions of both halves live in this skill's `scripts/`:
`scripts/oc_dispatch.py <prompt-file>` (prints the new session id) and
`scripts/oc_collect.py <session-id> [timeout]` (waits, approves permissions,
prints final text). Copy them to /tmp or run them from the skill dir.

**Collect later** — reuse `oc`'s own SSE wait + permission-approval loop by
importing it as a module. `/usr/local/bin/oc` has **no `.py` extension**, so
`import oc` fails AND `importlib.util.spec_from_file_location` returns a None
loader. Use `SourceFileLoader` instead:

```python
from importlib.machinery import SourceFileLoader
o = SourceFileLoader('oc', '/usr/local/bin/oc').load_module()
o.wait_for_completion(sid, set(), 3600)   # blocks until done, prints final text
msgs = o.messages(sid)                    # newest-first message list
o.approve_permissions(sid)                # clear pending permission backlog
```

**Blocked sessions**: when a session ends on a decision point (final assistant
text asks a question instead of reporting results), answer in-session with
`oc --session <sid> "$(cat /workspace/followup.md)"` — the session keeps full
context, no need to re-state the task. Same trick works for the dispatch
pattern: just POST another prompt to the same sid.

**Zombie execution — dead turn that accepts prompts but never runs them**
(validated 2026-08-11): distinct from the `/api/session/active` zombies above
(those are sessions stuck *running*; this one is silently *dead*). Signature:
the newest assistant message has `finish=None` AND `time.completed=None` (its
turn never finished), `GET /api/session/{id}/permission` returns empty
`data: []`, and `GET /api/session/{id}/question` returns empty — so there is
nothing to approve and nothing to answer. A follow-up prompt sent via
`oc --session <sid>` or the prompt endpoint is *accepted* (HTTP 200) but never
executes: the message count stays unchanged and the waiter times out.
Diagnosis: fetch messages, check the newest turn's `finish`/`completed`
fields; if both are None and the message count doesn't grow after posting a
follow-up, the execution is dead. **Recovery: abandon the session and
re-dispatch a FRESH session** with a self-contained prompt that bakes in
everything the dead session already discovered (findings, approved decisions,
pre-flight facts) so it doesn't re-hit the same blocker and stall on the same
question. Do not keep retrying the follow-up — a dead execution does not
revive, and the `oc` wrapper will just burn its timeout again.

**Server-wide execution wedge (found 2026-08-15) — interrupt does NOT fix
this one.** Signature: *every* new session accepts prompts (prompt endpoint
returns 200) but executes nothing — no assistant parts, no tool calls, `finish=None`
forever; even a trivial "Reply PONG" session sits dead. No provider error
(`finish:"error"` absent), no pending permissions/questions, `/api/session/active`
clean after interrupting prior zombies. Progression observed: one session
zombied mid-task first; after interrupt + fresh re-dispatch, the fresh session
died on arrival; a PONG test confirmed the execution engine itself was wedged.
This is a SERVER bug state, not a session problem — session-level recovery
(zombie interrupt, re-dispatch, permission clearing) is useless.

**Hanging-provider impostor (refined 2026-08-15):** a "server-wide wedge"
PONG diagnosis is only valid if the probe pinned a KNOWN-GOOD provider. A
provider endpoint that accepts the request then never responds (expired
token plan, dead MaaS route) produces the identical signature —
`finish=None` forever, no error body — and is NOT fixed by server restart.
Always run the PONG test on two providers before concluding server wedge:
e.g. `glm-5.3@zai-coding-plan` (clean `finish=stop, text=PONG`) vs
`qwen3.8-max@bailian-personal` (hangs → provider dead, swap models, don't
restart). On this box (Aug 2026) bailian hung for weeks on an exhausted token plan — quota resets revive it (validated 2026-08-16: clean `finish=stop, PONG` on qwen3.8-max@bailian right after the user reset the quota; re-PONG before re-pinning a provider after any billing change). An OpenCode-gateway free
model can 401 ("not supported") independently — that's a gateway auth
issue, not the engine.

**Recovery when engine IS wedged** (engine-wide: every provider hangs,
health endpoint still answers): restart the OpenCode server host-side** (`systemctl --user restart
hermes-opencode`), then verify with a fresh PONG session **pinned to a
known-good provider** before re-dispatching real work. Ready-made probe:
`scripts/oc_ping.py [model] [provider]` (defaults to the known-good
glm-5.3@zai-coding-plan pair) — run it twice with different providers for
the split test; it prints the verdict plus `finish`/`error` detail and
cleans up its own test session. Also use it as a **pre-dispatch check**
whenever the previous dispatch died without output: pin the exact
model/provider pair you're about to dispatch real work to.

**Re-confirmed 2026-08-15** on a 60-min migration-refactor task: the zombie
died mid-RESEARCH (no files written — verify with `git status` + the
write-set check before assuming partial work), the follow-up prompt sat in
`/pending` indefinitely, and even a trivial status-report prompt timed out.
Two API-shape facts: `GET /api/session/active` returns a MAP keyed by session
id (`data: {"ses_...": {"type": "running"}}`), not a list — iterate
`.items()`. And the dead turn showed `finish=None, completed=None,
parts=[]`. One-shot health check: `scripts/oc_diagnose.py <session-id>`
(active map + /pending + newest-turn health). Note: when recovery needs
`POST /api/session/{id}/interrupt`, the Hermes terminal approval gate may
block the call — surface it to the user instead of retrying silently.

## Adversarial-review dispatch pattern (cross-model, validated 2026-08-16)

For the user's review discipline (every implementation gets an adversarial
review; **the reviewer model must differ from the author model**):

1. **Identify the author first**: `GET /api/session/{id}` → `data.model` =
   `{id, providerID}`. Never assume from defaults — task d97d74f turned out
   to be authored by qwen3.8-max@bailian even though glm-5.3@zai authored the
   sibling tasks in the same chain.
2. **Pin the reviewer explicitly**: `oc --model <other> --provider <other>`.
   Never rely on the server default (on this box it is
   bailian-personal/qwen3.8-max per `GET /api/config` → `info.model`; agents
   can have their own defaults — `agents.build.model` was qwen3.7-plus — so
   unpinned sessions are unpredictable). Always pin implementation dispatches
   too.
3. **Read-only charter** (no dedicated review agent exists — agents are
   Build/Plan/General/Explore/…): open with "READ-ONLY adversarial code
   review — do NOT modify, stage, or commit any file; your sole deliverable
   is a report". Name the commit sha, the spec file, and prior review files;
   list targeted hunt questions per commit; demand "VERDICT: SHIP or FIX
   FIRST" + numbered findings with severity and file:line evidence, argued
   at full strength.
4. Reviewers will re-verify independently when prompted (both qwen3.8-max and
   glm-5.3 re-ran the test suite and re-checked the live DB read-only in
   ~10 min/report) — encourage it.
5. File reports at `/workspace/reviews/review-<sha>.md`; post verdicts as
   kanban comments. Remediation commits get their own review cycle
   (fix → re-review → iterate until SHIP).

## The OpenAPI spec is the source of truth

`GET /openapi.json` on the OpenCode server returns the full API surface — every
endpoint, method, schema. **Always consult it first** when you need an endpoint
not covered by `oc`/`ocm`. The spec lists ~80 routes including session lifecycle,
events, VCS, MCP, PTY, permissions, forms, and more.

Authentication: Basic auth with `OPENCODE_SERVER_USER` and
`OPENCODE_SERVER_PASSWORD` env vars. Server URL in `OPENCODE_SERVER_URL`
(typically `http://hermes-opencode:45650`).

## Model selection pitfalls

### `tools: false` is spurious — a config artifact, NOT a real limitation

All models under `bailian-personal` and `bailian-team` providers report
`capabilities.tools: false` in OpenCode's registry. **This is wrong.** Alibaba's
official Function Calling docs
(https://www.alibabacloud.com/help/en/model-studio/qwen-function-calling)
explicitly list all these model families as supporting native function calling:

- Qwen-Max: Qwen3.7-Max, Qwen3.6-Max series
- Qwen-Plus: Qwen3.7-Plus, Qwen3.6-Plus series
- Qwen-Flash: Qwen3.6-Flash series

OpenCode's `opencode-v2.json` config does not declare `capabilities` for
manually-configured bailian models, so the registry defaults to `false`. The
`@ai-sdk/anthropic` adapter actually supports tools fine — SSE event traces
confirm `session.tool.called` → `session.tool.success` flowing through the
adapter with bailian models.

**Fix:** Add `capabilities: {tools: true, input: ["text", "image"], output: ["text"]}`
to each model entry in `opencode-v2.json`. Until then, ignore the `tools: false`
flag — the models work correctly with tool calls regardless.

### Registering new model IDs in opencode-v2.json

The model list is defined in `~/.config/opencode/opencode-v2.json` on the host
(mounted read-only into the OpenCode container). New upstream model IDs (e.g.
`qwen3.8-max` when it graduates from `-preview`) must be manually added to the
`providers.<id>.models` section. There is **no API** to register models — only
the config file.

When a model ID exists in the config but the upstream API doesn't recognize it
yet, the session returns `provider.no-route: Model unavailable`. When the model
is not in the config at all, same error.

After editing the config:
```bash
cp <updated>.json ~/.config/opencode/opencode-v2.json
systemctl --user restart hermes-opencode
```

### Provider exhaustion and fallback (found 2026-08-11)

When `oc --model <id> --provider <pid>` returns `agent error: unknown`
immediately with zero messages in the session, the provider is likely
returning errors. Check the raw message: `GET /api/session/{sid}/message` —
look for `finish: "error"` with `error.type: "provider.rate-limit"` and a
429 body like `"Insufficient balance or no resource package. Please
recharge."`

**Enumerate all providers**: `GET /api/provider` (with Basic auth) returns
the full list with IDs, names, package types, and base URLs. Each provider
has independent billing — exhaustion of one does not affect others.

Known provider landscape (Aug 2026):

| Provider ID | Name | Package | Notes |
|---|---|---|---|
| `zai` | Z.AI | `@ai-sdk/openai-compatible` | Main Zhipu account. Can run out of balance (429). |
| `zai-coding-plan` | Z.AI Coding Plan | `@ai-sdk/openai-compatible` | **Separate billing** from `zai`. Same models. Reliable fallback when `zai` is exhausted. |
| `zhipuai` | Zhipu AI | `@ai-sdk/openai-compatible` | Direct Zhipu (bigmodel.cn). |
| `zhipuai-coding-plan` | Zhipu AI Coding Plan | `@ai-sdk/openai-compatible` | Separate coding plan billing. |
| `bailian-personal` | Personal Token Plan | `@ai-sdk/anthropic` | Alibaba personal token plan. |
| `bailian-team` | Team Token Plan | `@ai-sdk/anthropic` | Alibaba team token plan. |
| `opencode` | OpenCode Zen | `@ai-sdk/openai-compatible` | Public API, `apiKey: "public"`. |

When a user-requested model/provider is exhausted, try the same model ID on
the `-coding-plan` variant (e.g. `glm-5.2` on `zai-coding-plan` instead of
`zai`). The `coding-plan` providers share the same model catalog but bill
separately. Always verify the fallback works with a trivial prompt before
dispatching a real task.

The `oc` script default is `OC_MODEL_ID` (currently `qwen3.8-max`). The server
default comes from the `"model"` field in `opencode-v2.json`. These can differ —
always check `GET /api/model/default` to see what the server will use for
sessions created without an explicit `--model` flag.

## Timeout debugging checklist

**First principle: a timeout exit kills the client wrapper, not the
server-side session.** When `oc` reports "timed out waiting for OpenCode to
finish" (exit 1 or 3) or `terminal` times out (exit 124), the agent is
almost certainly still running server-side. Treat the timeout as a "client
desynchronized" event, not a task failure. Reattach or collect.

Only use this checklist when the session shows signs of being genuinely stuck
(a tool part stuck in `running` for 10+ minutes on a trivial read, or no
progress at all). If the agent is still advancing through messages, the
correct action is to give it more time via re-collection, not to interrupt it.

1. **Check for unregistered model IDs.** If the model isn't in `opencode-v2.json`,
   the session fails instantly with `provider.no-route: Model unavailable`. Check
   the session event log at `GET /api/experimental/session/{id}/log` for the error.
   The `tools: false` flag in the registry is spurious — see the model section above.

2. **Check for zombie sessions.** `GET /api/session/active` lists sessions stuck
   in "running" state. Six zombies were observed holding server resources and
   causing contention on complex tasks. Clean up with
   `POST /api/session/{id}/interrupt` (returns 204). Do NOT use
   `DELETE /api/session/{id}` for live zombies — it removes the session entirely
   rather than gracefully stopping it.

3. **Check pending work.** `GET /api/session/{id}/pending` reveals unprocessed
   prompts (e.g. `"steer"` delivery messages) that keep a session "running"
   indefinitely and cause `POST /wait` to block.

4. **Use background terminal or HTTP API for long tasks.**
   `oc` (both the custom wrapper and `opencode2 run`) is blocking-only.
   For tasks beyond the CLI timeout ceiling: `terminal(background=true,
   notify_on_complete=true)` + `oc --timeout 2400 "task"` — the task
   outlives the foreground terminal limit and you're notified on completion.
   For true fire-and-forget: dispatch via `POST /api/session/{sid}/prompt`
   (non-blocking), collect later via `oc_collect.py` — see the
   opencode-async skill.

5. **Check the session's message trail.** `GET /api/session/{id}/message?order=desc&limit=20`
   — look for repeated tool calls (stuck loop) or error messages.

## The `oc` wait mechanism — SSE-driven

The wait logic uses the SSE strategy
(`GET /api/event`) provides near-zero-latency completion detection. A background
thread approves permissions every 2s. If SSE drops or is unavailable, it falls
back to the original `sleep(2)` polling loop.

**Key SSE events for completion detection:**

See `references/opencode-sse-events.md` for the full event taxonomy with
Python reading code.

| Event | Meaning |
|-------|---------|
| `session.execution.started` | Agent loop begins |
| `session.step.started` | Individual reasoning+tool cycle begins |
| `session.step.ended` | Step done (check `finish` field: `stop` = terminal, `tool-calls` = more steps) |
| `session.execution.succeeded` | **Terminal: agent loop complete** |
| `session.execution.failed` | **Terminal: error** (check `data.error`) |
| `: heartbeat` | Keepalive (ignore) |

**The `finish` field on `session.step.ended`:**
- `"stop"` — final answer, no more tool calls
- `"tool-calls"` — intermediate step, more messages coming
- Other values — see OpenAPI spec

**Permission events via SSE:** `session.permission.requested` events arrive in
real time, but the current implementation polls `/permission` every 2s in a
background thread instead. Future improvement: react to SSE events for instant
approval.

**Permission reply enum pitfall (found 2026-08-10):** the reply body for
`POST /api/session/{id}/permission/{requestID}/reply` accepts ONLY
`{"reply": "once" | "always" | "reject"}` (schema `PermissionV2.Reply` in
`/openapi.json`). The `oc` wrapper originally sent `{"reply": "approve"}`,
which 400s — and `req()` swallows HTTP errors into `{"_error": ...}` dicts, so
every approval FAILED SILENTLY. Symptom: session stuck "running" forever with
`GET /api/session/{id}/permission` showing a pending `external_directory`
request (any read outside the session's project root, e.g. `/workspace/*` from
a `/work`-rooted session). Fix landed in `hermes-pod/bin/oc`
(approve_permissions): try `always` first (persists permission project-wide so
headless runs never re-prompt), fall back to `once`. Diagnose with:
`GET /api/permission/request` (global pending), `GET /api/permission/saved`,
and approve manually via the reply endpoint with `"always"`.

### Verifying a delegated session's output

A session's self-report ("Done, files changed: X, Y") is a claim, not proof —
verify it against the server and the repo:

1. **Write-set via API**: `GET /api/session/{id}/message?order=asc&limit=500`,
   then collect `content[]` tool parts whose name is `edit`/`write`/`patch`/
   `multiedit` — `state.input.filePath` (or `.path`/`.file`) is every file the
   session touched. Compare against `git status --short` in the target repo.
   Anything dirty that is NOT in the write-set is pre-existing uncommitted
   work from earlier sessions — attribute it correctly instead of blaming (or
   crediting) this session. File mtimes (`stat -c "%y %n"`) corroborate.
2. **Session done ≠ files landed**: if `oc` timed out (exit 1/124) the server
   session may have kept running. Check `GET /api/session/active` + message
   trail before re-dispatching — a re-dispatch over a half-finished tree
   duplicates work.
3. **Watch long sessions**: poll `GET /api/session/active` every ~30s in a
   background process (`terminal background=true, notify_on_complete=true`);
   the session leaving the active list is the completion signal. Print the last
   assistant text part for the final report. (Note: `/api/session/{id}/message`
   returns HTTP 400 once a session leaves the active list — grab final output
   from `/pending`-free reads BEFORE that, or tolerate the 400 in the watcher.)
4. **Run the checks yourself**: syntax-check edited files (`python3 -c
   'import ast; ast.parse(...)'`), and if the task has a spec file, diff the
   result against it section by section. Don't accept "all checks pass" from
   the session as sufficient.

### Blocking wait: `POST /api/session/{id}/wait`

A simpler alternative: `POST /api/session/{id}/wait` blocks until the session's
agent loop becomes idle, then returns 204.

**Caveat:** If the session has zombie pending work, `/wait` blocks indefinitely.
Always check `/pending` first when using this approach.

## Key API endpoints quick reference

See `references/opencode-server-api.md` for the full table with diagnostics
context. Most useful beyond `oc`/`ocm`:

- `GET /api/session/active` — find zombie sessions
- `GET /api/session/{id}/pending` — find stuck work
- `GET /api/model/default` — verify which model new sessions get
- `GET /api/event` (SSE) — real-time event stream
- `POST /api/session/{id}/wait` — blocking idle wait
- `GET /api/session/{id}/message?order=desc&limit=N` — paginated message
  history. Payload shape: `data[]` messages with `type` (user/assistant) and
  `content[]` parts typed `text|reasoning|tool`; tool parts carry
  `state.status` (a trivial read stuck in `running` for 10+ min = wedged
  session) and `state.input`. The keys are NOT `role`/`parts` — inspection
  scripts assuming those return empty.

## Container/host path mapping

When you produce artifacts the user must access from the host, **always give the
host-side path** — container paths are invisible to them. Container `/workspace`
maps to `~/Src/hermes-pod/hermes-workspace/` on the host. Full mapping table and
tracing instructions: see `references/container-host-path-mapping.md`.

## Overlap note

This skill overlaps with `opencode-driver` in model-selection guidance. Both
skills now reference `qwen3.8-max` as the default model. The model-selection
pitfalls (capabilities, registration, defaults) are canonical here; the
`opencode-driver` skill covers usage patterns (`oc`/`ocm` commands).
