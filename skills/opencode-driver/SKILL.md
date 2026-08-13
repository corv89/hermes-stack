---
name: opencode-driver
description: "Delegate coding to the shared OpenCode v2 server (features, refactors, fixes, review) via targeted API-CLI commands (`oc`, `ocm`). One-shot and multi-turn."
version: 3.2.0
author: hermes-stack contributors
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [Coding-Agent, OpenCode, Autonomous, Refactoring, Code-Review]
    related_skills: [claude-code, codex, hermes-agent]
---

# OpenCode Driver (API-first)

Drive the shared OpenCode v2 server through targeted CLI commands that wrap its
HTTP API: **`oc`** (run/continue coding tasks) and **`ocm`** (switch model/agent).
This is the primary interface — structured, deterministic, and reliable.

The server runs in a sibling container with the project mounted **read-write** at
`/work`. You (Hermes) see the same project **read-only** at `/work`; all edits
happen on the OpenCode server, never locally. Both containers also mount a shared
`/workspace` directory — you own it read-write, OpenCode sees it read-only (see
"Shared workspace" below). Authentication is automatic via the `OPENCODE_PASSWORD`
environment variable.

## Environment (what OpenCode can use)

The OpenCode container has this developer tooling available for the tasks you
delegate: `git`, `ripgrep` (`rg`), `fd-find` (`fdfind`), `python3` (3.11) with
`venv`/`pip`/dev headers, `build-essential` (gcc/make), `uv`/`uvx`, and
`node`/`npm`. So you can ask OpenCode to run Python, manage environments and
dependencies with `uv`, build C extensions, run tests, and so on. (The project
root `/work` is not itself a git repo; individual sub-projects under it are.)

## Shared workspace (`/workspace`)

Both containers also mount a shared directory at `/workspace`, with split
ownership: **you (Hermes) own it read-write; OpenCode sees it read-only.** It is
your scratch space for plans, specs, notes, and large context — the single-writer
mirror of `/work` (which only OpenCode writes).

- **Hand off context by file, not by giant prompt.** Write your plan/spec to
  `/workspace/<name>.md`, then reference it: `oc "Implement the plan in
  /workspace/plan.md"`. OpenCode reads `/workspace`, so it picks the file up.
- **OpenCode cannot write `/workspace`.** Its output is code (which lands in
  `/work`) plus the text reply on `oc` stdout. To keep an artifact OpenCode
  produces (a report, diff summary, analysis), capture the reply and write it to
  `/workspace` yourself.

## When to Use

- The user asks to implement / refactor / fix / review code in the project.
- You want an external coding agent to perform the file edits.
- One-shot tasks and multi-turn / long-running coding sessions.

## One-shot tasks (`oc`)

For a single bounded task, run it on a fresh session. The reply goes to stdout;
the session id goes to stderr (capture it if you'll continue the session):

```
terminal(command="oc \"Add retry logic to the API client and update the tests\"", workdir="/work")
```

Choose the model/agent for the new session with flags (or the `OC_MODEL_ID`,
`OC_MODEL_PROVIDER`, `OC_AGENT` env vars):

```
oc --model qwen3.8-max "review the scheduler for races"
oc --provider bailian-personal --model qwen3.7-plus "refactor X"
oc --agent plan "design the schema for the billing service"
```

## Multi-turn sessions (`oc --session` / `oc --continue`)

Sessions persist on the server, so you can iterate without the TUI. Capture the
`[oc] session=<id>` line from the first call, then continue that exact session:

```
# turn 1 (capture the session id printed to stderr)
oc "Implement OAuth refresh flow"            # -> [oc] session=ses_abc123

# turn 2..n: continue the same session
oc --session ses_abc123 "Now add error handling for token expiry"
oc --session ses_abc123 "Add tests for the expiry path"
```

`oc --continue "..."` continues the most recently updated session (convenient, but
prefer the explicit `--session <id>` for reliable chaining). A continued session
keeps its model/agent; change them with `ocm` (below). `oc` always waits for the
*new* reply to your prompt, not the previous turn's answer.

## Choosing / switching model & agent (`ocm`)

For a NEW session, use `oc --model/--provider/--agent` (above). For an
existing/continued session, switch via `ocm`, which talks to the API directly
(defaults to the most recently used session):

```
ocm qwen3.8-max                 # switch model (default provider bailian-personal)
ocm bailian-personal/qwen3.7-plus       # explicit provider via slash
ocm qwen3.8-max --provider X    # explicit provider flag
ocm --agent plan                        # switch agent (build/plan)
ocm qwen3.8-max --agent plan    # both at once
ocm --list                              # show sessions (id, agent, model)
ocm <model> --session <ses_id>          # target a specific session
```

The change applies to subsequent prompts in that session.

## Permissions & questions

`oc` auto-approves OpenCode permission requests (file writes, shell commands) so
tasks run to completion. If a task requires an interactive decision, OpenCode may
raise a question/form; for those advanced cases the session question/form API
endpoints exist, but routine coding tasks are handled automatically.

## Verification

The project is read-only for you, but it reflects OpenCode's edits. Verify by:
- inspecting the read-only mount, e.g. `git -C /work/<repo> diff --stat` (run git
  inside the specific sub-project — `/work` itself is not a git repo), and/or
- asking OpenCode to report/show the changes: `oc --session <id> "show the diff of what you changed"`.

## Constraints — read carefully

- **The project (`/work`) is mounted READ-ONLY for you.** Never edit source files
  yourself; delegate all edits to OpenCode via `oc` (which holds the read-write
  mount). The one place you write is `/workspace` (your scratch/output dir,
  read-only for OpenCode) — use it for plans and for keeping artifacts from
  OpenCode's replies.
- **Never use `/api/shell` or any raw shell endpoint** on the OpenCode server.
  Use `oc` and `ocm` only.
- The OpenCode server is the safety boundary; it runs in its own container.

## TUI — debug / fallback ONLY

Do **not** drive the TUI routinely. It is a fullscreen app whose overlays (model
picker Ctrl+X M, command palette, selectors) do not render through the PTY bridge.
Use it only to attach for debugging:

```
terminal(command="opencode2 --server http://hermes-opencode:45650", workdir="/work", background=true, pty=true)
# exit with Ctrl+C: process(action="write", session_id="<id>", data="\x03")
```

For all real work, use `oc` / `ocm`.
