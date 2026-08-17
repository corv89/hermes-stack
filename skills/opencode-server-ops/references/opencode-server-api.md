# OpenCode Server API — Endpoints & Diagnostics Detail

Supplements `opencode-server-ops` SKILL.md. Session-accumulated reference from
2026-08-08 investigation.

## Server endpoints (from GET /openapi.json)

~80 routes total. Key endpoints beyond what `oc`/`ocm` use:

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/event` | SSE stream — real-time events (message.created, session.updated, permission.requested). Content-Type: text/event-stream. |
| POST | `/api/session/{id}/wait` | Blocking wait — returns 204 when session agent loop is idle. Long-poll design. |
| GET | `/api/session/active` | Sessions currently in "running" state. Use to find zombies. |
| GET | `/api/session/{id}/pending` | Unprocessed prompts/work queued for a session. |
| GET | `/api/model/default` | Server's configured default model. |
| GET | `/api/model` | Full model catalog with capabilities, limits, status. |
| GET | `/api/provider` | Configured providers, base URLs, adapters. |
| GET | `/api/session/{id}/message` | Messages; supports `?order=desc&limit=N&cursor=<opaque>` pagination. |
| DELETE | `/api/session/{id}` | Delete a session (clean up zombies). |
| POST | `/api/session/{id}/interrupt` | Interrupt a running session. |
| POST | `/api/session/{id}/compact` | Compact session context. |
| GET | `/api/session/{id}/context` | Session context info. |
| GET | `/api/session/{id}/log` | Session log (experimental path). |

## Provider/Model catalog (2026-08-08 snapshot)

### bailian-personal (Anthropic-compatible adapter)
All models: `tools: false`, `@ai-sdk/anthropic` adapter,
baseURL: `token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic/v1`

| Model | Context | Output | Notes |
|-------|---------|--------|-------|
| qwen3.8-max-preview | 983616 | 131072 | Server default at time of writing |
| qwen3.7-max | 131072 | 65536 | |
| qwen3.7-plus | 131072 | 65536 | `oc` script default |
| qwen3.6-plus | 131072 | 65536 | |
| qwen3.6-flash | 131072 | 65536 | |

### bailian-team
Same bailian-personal models plus additional: deepseek-v4-pro/flash, v3.2,
kimi-k2.7-code, kimi-k2.6, kimi-k2.5, glm-5.2 (ctx 1M), glm-5.1, glm-5,
MiniMax-M2.5. All `tools: false`.

### opencode (Zen — free, tools: true)
26 models at time of writing. Notable active (non-deprecated):

| Model | Context | Output | Family |
|-------|---------|--------|--------|
| ling-3.0-tiny-free | 262144 | 32768 | ling |
| deepseek-v4-flash-free | 200000 | 128000 | deepseek-flash |
| laguna-s-2.1-free | 256000 | 32000 | laguna |
| longcat-2.0-free | 1000000 | 131072 | longcat |
| nemotron-3-ultra-free | 1000000 | 128000 | nemotron |
| north-mini-code-free | 256000 | 64000 | north |
| mimo-v2.5-free | 200000 | 32000 | mimo |
| big-pickle | 200000 | 32000 | big-pickle |

## Zombie session diagnosis

Observed: 4 sessions stuck in "running" state with one containing a pending
"steer" delivery prompt (`"run /workspace/apply_auxiliary_config.py"`) that was
never processed. This kept `/wait` blocking indefinitely.

Diagnostic flow:
1. `GET /api/session/active` — see what's "running"
2. For each: `GET /api/session/{id}/pending` — check for stuck work
3. If zombie: `DELETE /api/session/{id}` to clean up
4. Or: `POST /api/session/{id}/interrupt` to stop the agent loop

## MCP architecture note

OpenCode has MCP *client* support (`GET /api/mcp`, `PUT /api/mcp/{server}`).
The gap for this setup is an MCP *server* that wraps OpenCode's own API —
allowing Hermes to call OpenCode as a native tool with SSE-driven async delivery
rather than shelling out to the `oc` script with its sleep-based polling.

This is the proposed long-term architecture, not yet implemented.
