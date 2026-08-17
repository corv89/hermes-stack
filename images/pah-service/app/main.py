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
  GET  /healthz  {"status": "ok", "harness": <driver pin>, "records": N}
  POST /run      {repo, task, max_revisions?, timeout_s?} → pipeline verdict
  POST /plan     {repo, task, timeout_s?}                  → spec text
  POST /doctor   → per-key status (never key material)
  POST /show     {run_id} → the stored run record, verbatim

Exit→HTTP: 200 pass, 224 fail-verdict, 225 revision-limit, 400 validation,
500 infra (driver exit 3 / timeout / spawn failure). Responses never
include env or key material.

Concurrency: simultaneous tasks are simultaneous subprocesses — separate
processes, no shared interpreter state; records.jsonl appends are
single-write lines, so no queue is needed at task cadence.
"""
from __future__ import annotations

import ast
import asyncio
import hmac
import json
import os
import re
import time
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


# --- driver subprocess ---------------------------------------------------------

async def spawn_driver(argv: list[str], task: str, timeout_s: int):
    """Run the vendored driver: list argv, no shell, cwd=/, task on stdin.
    Returns (exit_code, stdout, stderr, duration_s, timed_out); exit_code is
    None when the process could not be spawned at all."""
    t0 = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            str(VENV_PY), str(DRIVER), *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd="/",
        )
    except OSError as e:
        return None, "", f"spawn failed: {e}", 0.0, False
    try:
        out_b, err_b = await asyncio.wait_for(
            proc.communicate(task.encode()), timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        try:
            out_b, err_b = await proc.communicate()
        except Exception:  # noqa: BLE001
            out_b, err_b = b"", b""
        return (proc.returncode, out_b.decode("utf-8", "replace"),
                err_b.decode("utf-8", "replace"), time.monotonic() - t0, True)
    return (proc.returncode, out_b.decode("utf-8", "replace"),
            err_b.decode("utf-8", "replace"), time.monotonic() - t0, False)


def run_id_from(stdout: str) -> str | None:
    m = RUN_ID_RE.search(stdout)
    return m.group(1) if m else None


def tail(s: str, n: int = 2000) -> str:
    return s.strip()[-n:]


def infra_response(code, run_id, rec, err, out) -> JSONResponse:
    why = ("driver infra error (exit 3)" if code == 3
           else "driver did not run" if code is None
           else f"driver exit {code}")
    return JSONResponse(status_code=500,
                        content={"error": why, "run_id": run_id,
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
    code, out, err, dur, timed_out = await spawn_driver(argv, task, timeout_s)
    run_id = run_id_from(out)
    rec = find_record(run_id) if run_id else None
    if timed_out:
        return JSONResponse(
            status_code=500,
            content={"error": f"driver timed out after {timeout_s}s",
                     "run_id": run_id, "outcome": (rec or {}).get("outcome"),
                     "stderr_tail": tail(err or out)})
    if code in (0, 1, 2):
        verdict = (rec or {}).get("verdict") or {}
        payload = {
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
    return infra_response(code, run_id, rec, err, out)


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
    code, out, err, dur, timed_out = await spawn_driver(argv, task, timeout_s)
    run_id = run_id_from(out)
    rec = find_record(run_id) if run_id else None
    if timed_out:
        return JSONResponse(
            status_code=500,
            content={"error": f"driver timed out after {timeout_s}s",
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
                         "run_id": run_id, "spec_path": spec_path})
        return JSONResponse(status_code=200,
                            content={"run_id": run_id,
                                     "spec_path": str(spec_path),
                                     "spec_text": real.read_text()})
    return infra_response(code, run_id, rec, err, out)


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
