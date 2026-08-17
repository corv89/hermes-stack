# OpenCode SSE Event Taxonomy

The `GET /api/event` endpoint (`text/event-stream`) pushes real-time events.
Each event is a JSON object on a `data:` line. Events have `type`, `data`
(with `sessionID`), and optionally `durable` (with `aggregateID`, `seq`).

## Terminal events (for completion detection)

| Event | When | Action |
|-------|------|--------|
| `session.execution.succeeded` | Agent loop completed normally | Fetch messages, return result |
| `session.execution.failed` | Agent loop errored | Check `data.error` for details |

## Step lifecycle

| Event | Meaning |
|-------|---------|
| `session.step.started` | New reasoning+tool cycle begins. Has `assistantMessageID`, `agent`, `model`. |
| `session.step.ended` | Step done. Has `finish` field: `"stop"` = terminal answer, `"tool-calls"` = more steps coming. Also has `cost` and `tokens`. |

## Token streaming events

| Event | Meaning |
|-------|---------|
| `session.reasoning.started` | Model starts thinking |
| `session.reasoning.delta` | Incremental reasoning token (has `delta` field) |
| `session.reasoning.ended` | Model done thinking (has full `text` field) |
| `session.text.started` | Model starts output text |
| `session.text.delta` | Incremental text token |
| `session.text.ended` | Model done outputting text |

## Tool call events

| Event | Meaning |
|-------|---------|
| `session.tool.input.started` | Model starts composing tool call |
| `session.tool.input.delta` | Incremental tool call JSON |
| `session.tool.input.ended` | Tool call composed |
| `session.tool.called` | Tool dispatched |
| `session.tool.success` | Tool returned successfully |
| `session.tool.error` | Tool failed |

## Session lifecycle

| Event | Meaning |
|-------|---------|
| `session.input.admitted` | User prompt accepted into session |
| `session.input.promoted` | Prompt moved from pending to active |
| `session.instructions.updated` | System instructions computed |
| `session.usage.updated` | Token/cost counter updated |
| `session.renamed` | Session title auto-generated |

## Other events

| Event | Meaning |
|-------|---------|
| `server.connected` | SSE connection established |
| `: heartbeat` | Keepalive comment (not a data event) |
| `shell.created` | Shell command spawned (no `sessionID`) |
| `shell.exited` | Shell command finished |

## Filtering

Events for a specific session have `data.sessionID` matching the session ID.
Events without `sessionID` (like `shell.created`, `server.connected`) are global.
Filter on `data.sessionID == sid` to track one session's progress.

## Reading SSE in Python

```python
import http.client, urllib.parse, json, base64, os

S = "http://hermes-opencode:45650"
parts = urllib.parse.urlparse(S)
conn = http.client.HTTPConnection(parts.hostname, parts.port or 45650, timeout=60)
AUTH = base64.b64encode(f"opencode:{os.environ['OPENCODE_SERVER_PASSWORD']}".encode()).decode()
conn.request("GET", "/api/event", headers={
    "Authorization": f"Basic {AUTH}",
    "Accept": "text/event-stream",
})
resp = conn.getresponse()
# Read line by line — each event is a "data: {...}" line followed by blank line
while True:
    line = resp.readline()
    if not line:
        break  # connection closed
    decoded = line.decode(errors="replace").rstrip()
    if decoded.startswith("data: "):
        evt = json.loads(decoded[6:])
        # Process evt["type"], evt["data"]["sessionID"], etc.
```

Reconnect on connection close — the server may drop idle SSE connections after
~60s. A brief `time.sleep(1)` before reconnect prevents tight loops.
