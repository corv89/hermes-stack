# OpenCode server HTTP API — async dispatch/collect reference

Used when tasks exceed the blocking `oc` window. Base URL
`$OPENCODE_SERVER_URL` (e.g. `http://hermes-opencode:45650` on hermesnet).
Auth: HTTP Basic — user `opencode`, password from `OPENCODE_SERVER_PASS`
(this deployment; oc itself also reads `OPENCODE_SERVER_PASSWORD` /
`OPENCODE_PASSWORD`).

## Endpoints

| Endpoint | Method | Body | Returns |
|---|---|---|---|
| `/api/session` | POST | `{"agent":"build","model":{"id":MODEL,"providerID":PROVIDER}}` | `{"data":{"id":"ses_..."}}` |
| `/api/session` | GET | — | session list (`time.updated` → latest) |
| `/api/session/{sid}/prompt` | POST | `{"text":"..."}` | accepted; execution runs server-side |
| `/api/session/{sid}/message` | GET | — | messages; assistant DONE = `time.completed` set AND `finish != "tool-calls"`; reply text = `content[]` parts with `type=="text"` |
| `/api/session/{sid}/permission` | GET | — | pending permission requests (`{"data":[]}` = none) |
| `/api/session/{sid}/permission/{pid}/reply` | POST | `{"reply":"always"}` | approve; "always" persists project-wide, fallback `{"reply":"once"}` |
| `/api/session/{sid}/question` | GET | — | pending interactive questions (`[]` = none) |
| `/api/event` | GET (SSE) | — | terminal events per session: `session.execution.succeeded` / `session.execution.failed` (match `data.sessionID`) |
| `/api/provider` | GET | — | **Provider/model discovery.** Returns array of configured providers with `id`, `name`, `package`, `settings.baseURL`. **Call this first when you need to pick a model/provider** — don't guess. Useful provider IDs observed: `zai`, `zai-coding-plan`, `zhipuai`, `zhipuai-coding-plan`, `bailian-personal`, `bailian-team`, `opencode` (free public zen proxy). |

## Provider fallback strategy (2026-08-11)

Cloud model providers go out of balance (HTTP 429 "Insufficient balance").
The `coding-plan` variants (`zai-coding-plan`, `zhipuai-coding-plan`) often
have **separate billing** from the main plan and may work when the main
account is exhausted. When `oc` returns `agent error: unknown`, check the
session's assistant message via `/api/session/{sid}/message` — if
`finish=="error"` with `error.type=="provider.rate-limit"`, switch providers:

```sh
# 1. List available providers
curl -s -u opencode:$OPENCODE_SERVER_PASS \
  http://hermes-opencode:45650/api/provider | python3 -m json.tool

# 2. Try a coding-plan variant
curl -s -u opencode:$OPENCODE_SERVER_PASS http://hermes-opencode:45650/api/session \
  -X POST -H "Content-Type: application/json" \
  -d '{"agent":"build","model":{"id":"glm-5.2","providerID":"zai-coding-plan"}}'
```

Then use `oc --model <id> --provider <providerID>` for CLI tasks.

## Session-state diagnosis

- **Healthy running**: latest assistant message `finish=="tool-calls"`,
  `time.completed` timestamps advancing across polls.
- **Done**: `finish` set to a non-tool-calls value + `time.completed` set +
  text present.
- **ZOMBIE**: latest message has `finish=None` + `time.completed` null + no
  pending permissions + no questions + follow-up prompts add no messages.
  NOT recoverable by poking — abandon and start a fresh session with a
  self-contained prompt that bakes in everything the stalled session found.

## Reusing oc's helpers from Python

```python
from importlib.machinery import SourceFileLoader
o = SourceFileLoader("oc", "/usr/local/bin/oc").load_module()
# o.req(method, path, body)         — authed JSON request
# o.messages(sid)                   — message list (newest first)
# o.assistant_text(msg)             — joined text parts
# o.approve_permissions(sid)        — clear permission backlog
# o.wait_for_completion(sid, seen, timeout) — SSE+poll wait, prints final text
```
