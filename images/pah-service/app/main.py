"""hermes-pah service — FastAPI front for the vendored PAH driver.

In-process execution (v2 design, opencode-server pattern): ONE long-lived
container on hermesnet holds the mounts and keys; /run and /plan spawn the
vendored driver (/opt/pah-driver/pah.py) as a plain subprocess per task —
no runner containers, no podman socket, no host execution. Spec:
/workspace/skill-sync/pah-service-wiring-spec.md.

Filesystem perspective: the service mounts the same host tree the OC server
and the webui see, at the same string /work (rw this time). Workspaces are
passed to the driver as absolute /work/<repo> paths, so record fields
(`workspace`, `spec_path`) are born in the caller's perspective — no
translation layer exists to get wrong.

Routes — ALL require the X-PAH-Token header (hmac-compared to PAH_TOKEN):
  GET  /healthz          {"status": "ok", "harness": <driver pin>, "records": N}
  POST /run              {repo, task, max_revisions?, timeout_s?} → pipeline verdict
  POST /plan             {repo, task, timeout_s?}                  → spec text
  POST /doctor           → per-key status (never key material)
  POST /show             {run_id} → the stored run record, verbatim
  GET  /tasks            → live task-registry summaries
  GET  /status/{task_id} → one task entry incl. its stdout tail (sk- redacted)
  POST /cancel/{task_id} → SIGTERM the task's process group; mark cancelled

Exit→HTTP: 200 pass, 224 fail-verdict, 225 revision-limit, 400 validation,
409 repo busy (per-repo lock), 500 infra (driver exit 3 / timeout / spawn
failure). Responses never include env or key material.

Task registry: every /run and /plan mints a task_id and streams the
driver's stdout line-by-line into a bounded tail (deque, 50 lines) so a
caller can poll progress during AND after the run (GET /tasks,
GET /status/{task_id}). The registry is a live view only — records.jsonl
stays the durable log.

Concurrency: one repo, one task — /run and /plan claim the repo
(resolved-path key) atomically; a second request while one runs gets 409
{"error": "repo busy", "task_id": <running>}. Different repos run in
parallel as subprocesses in their own process groups. Locks are in-memory
(container lifetime); a restart clears them, which is safe because the
subprocess dies with the container.
"""
from __future__ import annotations

import ast
import asyncio
import hmac
import json
import os
import re
import signal
import threading
import time
import uuid
from collections import deque
from pathlib import Path, PurePosixPath

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

WORK_ROOT = Path("/work")
DRIVER = Path("/opt/pah-driver/pah.py")
VENV_PY = Path("/opt/venv/bin/python")
RECORDS = Path("/opt/pah-driver/runs/records.jsonl")
DEFAULT_TIMEOUT_S = 3600
MAX_TIMEOUT_S = 14400
DOCTOR_TIMEOUT_S = 120

PAH_TOKEN = os.environ.get("PAH_TOKEN", "")
if not PAH_TOKEN:
    # Refuse to boot unauthenticated: the token is quadlet-injected, so a
    # missing value means a misconfigured unit — starting anyway would hang
    # a /work-rw service off hermesnet with the door open.
    raise SystemExit("PAH_TOKEN not set — refusing to start (quadlet must inject it)")

RUN_ID_RE = re.compile(r"\[(\d{8}_\d{6}_[0-9a-f]{6})\]")


def _harness_pin() -> str:
    """HARNESS_VERSION read from the vendored driver — single source of truth."""
    for node in ast.walk(ast.parse(DRIVER.read_text())):
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "HARNESS_VERSION"
                        for t in node.targets)):
            return str(ast.literal_eval(node.value))
    return "unknown"


HARNESS = _harness_pin()

app = FastAPI(title="hermes-pah", version=HARNESS,
              docs_url=None, redoc_url=None, openapi_url=None)


class Bad(ValueError):
    """Request rejected by validation (→ HTTP 400)."""


class RunBody(BaseModel):
    repo: str
    task: str
    max_revisions: int | None = Field(default=None, ge=0, le=10)
    timeout_s: int | None = Field(default=None, ge=1, le=MAX_TIMEOUT_S)


class PlanBody(BaseModel):
    repo: str
    task: str
    timeout_s: int | None = Field(default=None, ge=1, le=MAX_TIMEOUT_S)


class ShowBody(BaseModel):
    run_id: str


@app.exception_handler(RequestValidationError)
async def _validation_to_400(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400,
                        content={"error": "invalid request body",
                                 "detail": exc.errors()})


async def auth(request: Request) -> None:
    provided = request.headers.get("x-pah-token", "")
    if not hmac.compare_digest(provided, PAH_TOKEN):
        raise HTTPException(status_code=401, detail="bad or missing X-PAH-Token")


# --- validation ---------------------------------------------------------------

def resolve_repo(repo: str) -> Path:
    """A /work-relative repo dir that realpath-contains under the service's
    /work and carries a .git (dir or worktree file)."""
    p = PurePosixPath(repo)
    if not repo or p.is_absolute() or ".." in p.parts:
        raise Bad(f"repo must be a /work-relative path, got: {repo!r}")
    root = WORK_ROOT.resolve()
    ws = (root / p).resolve()
    if ws == root or root not in ws.parents:
        raise Bad(f"repo {repo!r} resolves outside /work")
    if not ws.is_dir():
        raise Bad(f"repo {repo!r} not found under /work")
    if not (ws / ".git").exists():
        raise Bad(f"repo {repo!r} has no .git")
    return ws


def resolve_task(task: str) -> str:
    """Inline task text, or a /work-relative file path read HERE — no task
    file path is ever passed through to the driver."""
    if not task.strip():
        raise Bad("task is empty (inline text or a /work-relative file path)")
    if "\n" not in task:  # a file path never contains a newline
        root = WORK_ROOT.resolve()
        cand = Path(task) if task.startswith("/") else root / task
        try:
            real = cand.resolve()
        except (OSError, ValueError):
            return task
        if real.is_file() and root in real.parents:
            return real.read_text()
    return task


# --- records (pah-runs volume) -------------------------------------------------

def count_records() -> int:
    try:
        with RECORDS.open() as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def find_record(run_id: str) -> dict | None:
    try:
        lines = RECORDS.read_text().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:  # torn concurrent append — skip
            continue
        if isinstance(rec, dict) and rec.get("run_id") == run_id:
            return rec
    return None


# --- task registry (live view; records.jsonl stays the durable log) ------------

MAX_TAIL_LINES = 50
MAX_FINISHED_TASKS = 100
KEY_PREFIXES = ("sk-",)

REGISTRY_LOCK = threading.Lock()
TASKS: dict[str, dict] = {}


class RepoBusy(Exception):
    """The repo already has a running task (→ HTTP 409 repo busy)."""

    def __init__(self, holder_task_id: str):
        super().__init__(holder_task_id)
        self.holder = holder_task_id


def redacted(lines) -> list[str]:
    """Defense in depth: drop any line carrying a key prefix before it is
    returned to a caller. pah.py never prints keys; this guard exists so
    the registry cannot become a leak channel if that ever changes."""
    return [ln for ln in lines if not any(p in ln for p in KEY_PREFIXES)]


def _register_task(kind: str, repo: str, repo_path: str) -> tuple[str, str | None]:
    """Atomically mint a registry entry and claim repo_path. The claim IS
    the per-repo lock: a repo is held exactly while one of its tasks is
    `running`. Returns (task_id, None) on success, or ("", holder_task_id)
    when the repo is busy."""
    with REGISTRY_LOCK:
        for tid, e in TASKS.items():
            if e["repo_path"] == repo_path and e["status"] == "running":
                return "", tid
        task_id = uuid.uuid4().hex
        TASKS[task_id] = {
            "task_id": task_id,
            "kind": kind,
            "repo": repo,
            "repo_path": repo_path,
            "started_at": time.time(),
            "status": "running",
            "exit_code": None,
            "run_id": None,
            "last_lines": deque(maxlen=MAX_TAIL_LINES),
        }
        return task_id, None


def _finish_task(task_id: str, status: str, exit_code: int | None) -> None:
    """Terminal transition — never overwrites a concurrent /cancel — and
    prune finished entries to the most recent MAX_FINISHED_TASKS."""
    with REGISTRY_LOCK:
        e = TASKS.get(task_id)
        if e is None or e["status"] != "running":
            return
        e["status"] = status
        e["exit_code"] = exit_code
        finished = [tid for tid, t in TASKS.items() if t["status"] != "running"]
        finished.sort(key=lambda tid: TASKS[tid]["started_at"])
        drop = max(0, len(finished) - MAX_FINISHED_TASKS)
        for tid in finished[:drop]:
            TASKS.pop(tid, None)


def _task_summary(e: dict) -> dict:
    return {"task_id": e["task_id"], "kind": e["kind"], "repo": e["repo"],
            "status": e["status"], "exit_code": e["exit_code"],
            "age_s": round(time.time() - e["started_at"], 1),
            "run_id": e["run_id"]}


def _task_payload(e: dict) -> dict:
    """Full /status entry — internal keys (proc, cancelling, repo_path)
    stay out, and the tail goes through the redaction guard."""
    d = _task_summary(e)
    d["started_at"] = round(e["started_at"], 3)
    d["last_lines"] = redacted(list(e["last_lines"]))
    return d


# --- driver subprocess ---------------------------------------------------------

def _signal_pg(proc, sig: int) -> None:
    """Signal the subprocess's whole process group (start_new_session ⇒
    pgid == pid) so driver children die with it. Already-dead is fine."""
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


async def _spawn_driver_proc(argv: list[str]):
    """Vendored driver as a subprocess: list argv, no shell, cwd=/, task on
    stdin, OWN process group (start_new_session) so /cancel and timeouts
    reach the driver's whole tree, not just the python parent."""
    return asyncio.create_subprocess_exec(
        str(VENV_PY), str(DRIVER), *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd="/",
        start_new_session=True,
    )


async def _stream_proc(proc, task: str, timeout_s: int, on_line=None):
    """Feed the task on stdin, then read stdout line-by-line (each decoded
    line passed to on_line) with stderr drained concurrently — no bulk
    communicate(). The whole run is bounded by timeout_s; on timeout the
    process GROUP is SIGKILLed. Returns (exit_code, stdout, stderr,
    duration_s, timed_out)."""
    t0 = time.monotonic()
    deadline = t0 + timeout_s
    out_lines: list[str] = []
    err_b = b""
    timed_out = False
    try:
        proc.stdin.write(task.encode())
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass  # driver already gone — the read loop will see EOF
    err_task = asyncio.ensure_future(proc.stderr.read())
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), remaining)
            except asyncio.TimeoutError:
                timed_out = True
                break
            if not raw:
                break  # stdout EOF — driver is done talking
            text = raw.decode("utf-8", "replace").rstrip("\n")
            out_lines.append(text)
            if on_line is not None:
                on_line(text)
        if timed_out:
            _signal_pg(proc, signal.SIGKILL)
        try:
            await asyncio.wait_for(proc.wait(), 15)
        except asyncio.TimeoutError:
            pass
        try:
            err_b = await asyncio.wait_for(err_task, 15)
        except asyncio.TimeoutError:
            err_task.cancel()
    finally:
        if not err_task.done():
            err_task.cancel()
    return (proc.returncode, "\n".join(out_lines),
            err_b.decode("utf-8", "replace"), time.monotonic() - t0, timed_out)


async def spawn_driver(argv: list[str], task: str, timeout_s: int):
    """Untracked driver run (doctor). Returns (exit_code, stdout, stderr,
    duration_s, timed_out); exit_code is None when spawn itself failed."""
    try:
        proc = await _spawn_driver_proc(argv)
    except OSError as e:
        return None, "", f"spawn failed: {e}", 0.0, False
    return await _stream_proc(proc, task, timeout_s)


async def execute_tracked(kind: str, repo: str, repo_path: str,
                          argv: list[str], task: str, timeout_s: int):
    """Registry-tracked driver run for /run and /plan: mints the task_id,
    claims the repo (RepoBusy → 409), streams every stdout line into the
    task tail, parses [<run_id>] markers as they appear, and stays
    cancellable (POST /cancel SIGTERMs the process group).
    Returns (task_id, exit_code, stdout, stderr, duration_s, timed_out)."""
    task_id, holder = _register_task(kind, repo, repo_path)
    if holder is not None:
        raise RepoBusy(holder)
    entry = TASKS[task_id]

    def on_line(text: str) -> None:
        entry["last_lines"].append(text)
        m = RUN_ID_RE.search(text)
        if m and entry["run_id"] is None:
            entry["run_id"] = m.group(1)

    try:
        proc = await _spawn_driver_proc(argv)
    except OSError as e:
        _finish_task(task_id, "failed", None)
        return task_id, None, "", f"spawn failed: {e}", 0.0, False
    with REGISTRY_LOCK:
        entry["proc"] = proc
    try:
        code, out, err, dur, timed_out = await _stream_proc(
            proc, task, timeout_s, on_line)
    except asyncio.CancelledError:  # caller went away — never orphan a repo
        _signal_pg(proc, signal.SIGKILL)
        _finish_task(task_id, "failed", None)
        raise
    except Exception:  # noqa: BLE001 — same rule: no orphaned lock holders
        _signal_pg(proc, signal.SIGKILL)
        _finish_task(task_id, "failed", proc.returncode)
        raise
    finally:
        with REGISTRY_LOCK:
            entry.pop("proc", None)
    with REGISTRY_LOCK:
        cancelling = bool(entry.get("cancelling"))
        if entry["run_id"] is None:
            entry["run_id"] = run_id_from(out)
    if cancelling:
        _finish_task(task_id, "cancelled", code)
    elif timed_out:
        _finish_task(task_id, "timeout", code)
    elif code in (0, 1, 2):
        _finish_task(task_id, "done", code)
    else:
        _finish_task(task_id, "failed", code)
    return task_id, code, out, err, dur, timed_out


def run_id_from(stdout: str) -> str | None:
    m = RUN_ID_RE.search(stdout)
    return m.group(1) if m else None


def tail(s: str, n: int = 2000) -> str:
    return s.strip()[-n:]


def infra_response(code, run_id, rec, err, out, task_id=None) -> JSONResponse:
    why = ("driver infra error (exit 3)" if code == 3
           else "driver did not run" if code is None
           else f"driver exit {code}")
    return JSONResponse(status_code=500,
                        content={"error": why, "task_id": task_id,
                                 "run_id": run_id,
                                 "outcome": (rec or {}).get("outcome"),
                                 "stderr_tail": tail(err or out)})


# --- routes --------------------------------------------------------------------

@app.get("/healthz")
async def healthz(_: None = Depends(auth)) -> dict:
    return {"status": "ok", "harness": HARNESS, "records": count_records()}


@app.post("/run")
async def run_task(body: RunBody, _: None = Depends(auth)):
    try:
        ws = resolve_repo(body.repo)
        task = resolve_task(body.task)
    except Bad as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    timeout_s = body.timeout_s or DEFAULT_TIMEOUT_S
    argv = ["run", "--workspace", str(ws), "--task", "-"]
    if body.max_revisions is not None:
        argv += ["--max-revisions", str(body.max_revisions)]
    try:
        task_id, code, out, err, dur, timed_out = await execute_tracked(
            "run", body.repo, str(ws), argv, task, timeout_s)
    except RepoBusy as busy:
        return JSONResponse(status_code=409,
                            content={"error": "repo busy",
                                     "task_id": busy.holder})
    run_id = run_id_from(out)
    rec = find_record(run_id) if run_id else None
    if TASKS.get(task_id, {}).get("status") == "cancelled":
        return JSONResponse(status_code=500,
                            content={"error": "task cancelled",
                                     "task_id": task_id, "run_id": run_id})
    if timed_out:
        return JSONResponse(
            status_code=500,
            content={"error": f"driver timed out after {timeout_s}s",
                     "task_id": task_id,
                     "run_id": run_id, "outcome": (rec or {}).get("outcome"),
                     "stderr_tail": tail(err or out)})
    if code in (0, 1, 2):
        verdict = (rec or {}).get("verdict") or {}
        payload = {
            "task_id": task_id,
            "run_id": run_id,
            "verdict": verdict.get("verdict"),
            "revisions": (rec or {}).get("revisions"),
            "duration_s": round(dur, 1),
            "findings": verdict.get("findings", []),
        }
        if rec is None:
            payload["error"] = f"no run record found for {run_id}"
        return JSONResponse(status_code={0: 200, 1: 224, 2: 225}[code],
                            content=payload)
    return infra_response(code, run_id, rec, err, out, task_id)


@app.post("/plan")
async def plan_task(body: PlanBody, _: None = Depends(auth)):
    try:
        ws = resolve_repo(body.repo)
        task = resolve_task(body.task)
    except Bad as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    timeout_s = body.timeout_s or DEFAULT_TIMEOUT_S
    # No --out: the driver default lands at <workspace>/../spec-<ts>.md —
    # i.e. the /work root — writable and host-visible; the full text is also
    # returned so callers never need the file.
    argv = ["plan", "--workspace", str(ws), "--task", "-"]
    try:
        task_id, code, out, err, dur, timed_out = await execute_tracked(
            "plan", body.repo, str(ws), argv, task, timeout_s)
    except RepoBusy as busy:
        return JSONResponse(status_code=409,
                            content={"error": "repo busy",
                                     "task_id": busy.holder})
    run_id = run_id_from(out)
    rec = find_record(run_id) if run_id else None
    if TASKS.get(task_id, {}).get("status") == "cancelled":
        return JSONResponse(status_code=500,
                            content={"error": "task cancelled",
                                     "task_id": task_id, "run_id": run_id})
    if timed_out:
        return JSONResponse(
            status_code=500,
            content={"error": f"driver timed out after {timeout_s}s",
                     "task_id": task_id,
                     "run_id": run_id,
                     "stderr_tail": tail(err or out)})
    if code == 0:
        spec_path = (rec or {}).get("spec_path")
        if not spec_path:
            m = re.search(r"spec written: (\S+)", out)
            spec_path = m.group(1) if m else None
        root = WORK_ROOT.resolve()
        try:
            real = Path(spec_path).resolve() if spec_path else None
        except (OSError, ValueError):
            real = None
        if real is None or root not in real.parents or not real.is_file():
            return JSONResponse(
                status_code=500,
                content={"error": "spec file not found under /work",
                         "task_id": task_id,
                         "run_id": run_id, "spec_path": spec_path})
        return JSONResponse(status_code=200,
                            content={"task_id": task_id,
                                     "run_id": run_id,
                                     "spec_path": str(spec_path),
                                     "spec_text": real.read_text()})
    return infra_response(code, run_id, rec, err, out, task_id)


@app.post("/doctor")
async def doctor(_: None = Depends(auth)):
    code, out, err, dur, timed_out = await spawn_driver(
        ["doctor"], "", DOCTOR_TIMEOUT_S)
    if timed_out or code is None:
        return JSONResponse(
            status_code=500,
            content={"error": "doctor subprocess failed",
                     "stderr_tail": tail(err or out)})
    keys: dict[str, str] = {}
    for name in ("GLM_API_KEY", "ALIBABA_TOKEN_PLAN_API_KEY"):
        m = re.search(rf"^\s+{name}\b.*?:\s*(ok|MISSING)\s*$", out,
                      re.MULTILINE)
        keys[name] = m.group(1) if m else "unknown"
    return {"harness": HARNESS, "keys": keys,
            "ok": all(v == "ok" for v in keys.values()),
            "records": count_records(), "driver_exit": code}


@app.post("/show")
async def show(body: ShowBody, _: None = Depends(auth)):
    rec = find_record(body.run_id.strip())
    if rec is None:
        return JSONResponse(status_code=404,
                            content={"error": f"no record {body.run_id}"})
    return JSONResponse(status_code=200, content=rec)


@app.get("/tasks")
async def tasks(_: None = Depends(auth)) -> dict:
    with REGISTRY_LOCK:
        items = [_task_summary(e) for _, e in
                 sorted(TASKS.items(), key=lambda kv: kv[1]["started_at"])]
    return {"tasks": items}


@app.get("/status/{task_id}")
async def task_status(task_id: str, _: None = Depends(auth)):
    with REGISTRY_LOCK:
        entry = TASKS.get(task_id)
        payload = None if entry is None else _task_payload(entry)
    if payload is None:
        return JSONResponse(status_code=404,
                            content={"error": f"no task {task_id}"})
    return JSONResponse(status_code=200, content=payload)


@app.post("/cancel/{task_id}")
async def cancel_task(task_id: str, _: None = Depends(auth)):
    with REGISTRY_LOCK:
        entry = TASKS.get(task_id)
        if entry is None:
            return JSONResponse(status_code=404,
                                content={"error": f"no task {task_id}"})
        if entry["status"] != "running":
            return JSONResponse(status_code=200,
                                content={"task_id": task_id,
                                         "status": entry["status"],
                                         "cancelled": False})
        entry["cancelling"] = True
        proc = entry.get("proc")
    if proc is not None:
        _signal_pg(proc, signal.SIGTERM)
    _finish_task(task_id, "cancelled",
                 proc.returncode if proc is not None else None)
    return JSONResponse(status_code=200,
                        content={"task_id": task_id, "status": "cancelled",
                                 "cancelled": True})
