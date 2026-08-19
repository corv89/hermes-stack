#!/usr/bin/env python3
"""pah — Pydantic AI Harness driver: plan → implement → review.

Three frontier roles (user spec 2026-08-17):
  plan   GLM-5.3      (zai coding plan)    read-only
  code   GLM-5.3      (zai coding plan)    Coder workspace
  review Qwen3.8-Max  (Alibaba token plan) read-only, structured verdict

Every role gets the same workspace root; planner/reviewer are read-only by
construction (FilteredToolset, only read tools exposed). The reviewer returns
a structured verdict (pydantic output_type), not prose to grep.

Usage:
  pah run --workspace <dir> --task <file|->   full pipeline
  pah doctor                                  endpoint + config check
  pah show <run-id>                           print a run record

Run records (JSONL, one per line) go to <script_dir>/runs/records.jsonl for
the oc-vs-pah comparison. Keys are read from ~/.hermes/.env:
  GLM_API_KEY                    zai coding plan
  ALIBABA_TOKEN_PLAN_API_KEY     Alibaba token plan (sk-sp-)

Exit codes: 0 pass, 1 fail-verdict, 2 revision-limit, 3 infra error.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits

from pydantic_ai_harness import Coder
from pydantic_ai_harness.filesystem import FileSystem
from pydantic_ai.output import NativeOutput

SCRIPT_DIR = Path(__file__).resolve().parent
RUNS_DIR = SCRIPT_DIR / "runs"
RECORDS = RUNS_DIR / "records.jsonl"

ZAI_BASE = "https://api.z.ai/api/coding/paas/v4"
BAILIAN_BASE = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"

HARNESS_VERSION = "0.21.0"


def load_env() -> dict[str, str]:
    """Minimal .env loader (no dependency on python-dotenv)."""
    env_path = Path.home() / ".hermes" / ".env"
    out: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                v = v.strip()
                # strip matching surrounding quotes ('...' or "...")
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                out[k.strip()] = v
    return out


ENV = load_env()


def model_zai(model_id: str = "glm-5.3") -> OpenAIChatModel:
    key = os.environ.get("GLM_API_KEY") or ENV.get("GLM_API_KEY", "")
    if not key:
        sys.exit("GLM_API_KEY missing (zai coding plan)")
    return OpenAIChatModel(
        model_id,
        provider=OpenAIProvider(base_url=ZAI_BASE, api_key=key),
    )


def model_bailian(model_id: str = "qwen3.8-max") -> OpenAIChatModel:
    key = os.environ.get("ALIBABA_TOKEN_PLAN_API_KEY") or ENV.get(
        "ALIBABA_TOKEN_PLAN_API_KEY", ""
    )
    if not key:
        sys.exit("ALIBABA_TOKEN_PLAN_API_KEY missing (token plan)")
    return OpenAIChatModel(
        model_id,
        provider=OpenAIProvider(base_url=BAILIAN_BASE, api_key=key),
    )


# --- reviewer contract -------------------------------------------------


class Finding(BaseModel):
    severity: Literal["blocker", "major", "minor"] = "minor"
    file: str = ""
    description: str


class Verdict(BaseModel):
    verdict: Literal["pass", "fail"]
    summary: str = Field(description="one-paragraph assessment at full strength")
    findings: list[Finding] = Field(default_factory=list)
    fixes_attempted_if_any: str = ""


# --- roles --------------------------------------------------------------


def planner(workspace: Path) -> Agent:
    """Read-only planner: sees the repo, produces a spec. No writes, no shell."""
    fs = FileSystem(root_dir=workspace, read_only=True)
    return Agent(
        model_zai(),
        capabilities=[fs],
        instructions=(
            "You are the planning role. Inspect the workspace read-only and "
            "produce an implementation spec: files to touch, exact changes, "
            "verification commands. You do not modify anything. Output the "
            "spec as focused markdown, no preamble."
        ),
    )


def implementer(workspace: Path, allowed_commands: list[str]) -> Agent:
    """Coder stack: files + allowlisted shell + repo context + planning."""
    coder = Coder(
        workspace=str(workspace),
        allowed_commands=allowed_commands,
    )
    return Agent(
        model_zai(),
        capabilities=[coder],
        instructions=(
            "You are the implementation role. Follow the spec exactly. "
            "Commit nothing unless the spec says so. Run the spec's "
            "verification commands and include their output in your reply."
        ),
    )


def reviewer(workspace: Path) -> Agent:
    """Read-only adversarial reviewer with a structured verdict contract."""
    fs = FileSystem(root_dir=workspace, read_only=True)
    return Agent(
        model_bailian(),
        capabilities=[fs],
        # NativeOutput = JSON-schema response_format, no tool_choice in the
        # request. This keeps qwen3.8-max's thinking mode ON (required for
        # adversarial review quality) while preserving the structured verdict:
        # tool_choice=required (the default ToolOutput path) is rejected by
        # Bailian in thinking mode.
        output_type=NativeOutput(Verdict),
        instructions=(
            "You are the adversarial review role. Argue the change at full "
            "strength: correctness, edge cases, spec compliance, security. "
            "Only verify what you can see in the workspace; do not assume. "
            "Return the structured verdict. 'fail' only for blockers or "
            "majors; minors alone do not fail a change."
        ),
    )


# --- pipeline -----------------------------------------------------------


async def plan_only(workspace: Path, task: str, out_path: Path) -> int:
    """Emit an implementation spec for a task, touching nothing (pah plan)."""
    run_id = f"{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    log: dict = {
        "run_id": run_id,
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
        "workspace": str(workspace),
        "harness": HARNESS_VERSION,
        "roles": {"plan": "glm-5.3@zai"},
        "mode": "plan-only",
        "task": task[:500],
        "steps": [],
        "outcome": None,
    }
    spec = await run_step(log, {"plan": "glm-5.3@zai"}, "plan", planner(workspace), f"Task:\n{task}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(str(spec))
    log["outcome"] = "pass"
    log["spec_path"] = str(out_path)
    record(log)
    print(f"[{run_id}] spec written: {out_path}")
    return 0


def record(entry: dict) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    with RECORDS.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


async def run_step(
    log: dict, roles: dict[str, str], name: str, agent: Agent, prompt: str
):
    """Run one agent step, log timing/usage, re-raise on failure."""
    t0 = dt.datetime.now()
    try:
        result = await agent.run(
            prompt,
            # Long agentic runs exceed the default request_limit=50.
            # This is a runaway guard, not a budget; spend is recorded.
            usage_limits=UsageLimits(request_limit=1000),
        )
        usage = result.usage
        out = result.output
        log["steps"].append(
            {
                "step": name,
                "model": roles[name.split("-")[0]] if name.split("-")[0] in roles else "?",
                "seconds": (dt.datetime.now() - t0).total_seconds(),
                "request_tokens": usage.requests,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
            }
        )
        return out
    except Exception as e:  # noqa: BLE001
        log["steps"].append({"step": name, "error": f"{type(e).__name__}: {e}"})
        log["outcome"] = "infra-error"
        record(log)
        raise


async def run_pipeline(workspace: Path, task: str, max_revisions: int) -> int:
    run_id = f"{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    # OC-parity toolchain: uv/make/ruff are how real repos verify (uv run
    # pytest, make lint). The harness default already trusts these; the old
    # narrower list aborted runs on spec verification commands (2026-08-19,
    # sourcebot run 20260819_063441_1c5766). No shell (bash/sh) — the
    # allowlist gates what the model can spawn directly.
    allowed = [
        "python3", "python", "pytest", "uv", "make", "ruff",
        "ls", "cat", "git", "grep", "rg", "find", "sed", "head", "tail",
        "diff",
    ]
    # uv must never sync the repo's own .venv: host checkouts symlink their
    # interpreter to host uv paths, invisible in-container; an in-place
    # re-create would poison the host venv. Redirect to a per-repo
    # container-side environment (overridable for volume-mounted setups).
    venv_root = Path(os.environ.get("PAH_VENV_ROOT", "/tmp/pah-venvs"))
    repo_env = venv_root / workspace.name
    repo_env.mkdir(parents=True, exist_ok=True)
    os.environ["UV_PROJECT_ENVIRONMENT"] = str(repo_env)
    roles = {"plan": "glm-5.3@zai", "code": "glm-5.3@zai", "review": "qwen3.8-max@bailian"}
    log: dict = {
        "run_id": run_id,
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
        "workspace": str(workspace),
        "harness": HARNESS_VERSION,
        "roles": roles,
        "task": task[:500],
        "steps": [],
        "outcome": None,
    }

    async def step(name: str, agent: Agent, prompt: str):
        out = await run_step(log, roles, name, agent, prompt)
        return out

    # 1. plan
    spec = await step("plan", planner(workspace), f"Task:\n{task}")
    print(f"[{run_id}] plan done ({len(str(spec))} chars)")

    # 2. code (+ revision loop)
    report = await step(
        "code",
        implementer(workspace, allowed),
        f"Task:\n{task}\n\nSpec from the planning role (follow exactly):\n{spec}",
    )
    print(f"[{run_id}] code done")

    # 3. review (possibly repeated)
    verdict: Verdict = await step(
        "review",
        reviewer(workspace),
        f"Task:\n{task}\n\nSpec:\n{spec}\n\nImplementation report:\n{report}\n\n"
        "Review the workspace state adversarially and return the verdict.",
    )
    print(f"[{run_id}] review: {verdict.verdict} — {verdict.summary[:200]}")

    revisions = 0
    while verdict.verdict == "fail" and revisions < max_revisions:
        revisions += 1
        fixes = "\n".join(
            f"- [{f.severity}] {f.file}: {f.description}" for f in verdict.findings
        )
        report = await step(
            f"code-rev{revisions}",
            implementer(workspace, allowed),
            f"Original task:\n{task}\n\nOriginal spec:\n{spec}\n\n"
            f"The reviewer failed the change. Address every finding:\n{fixes}",
        )
        print(f"[{run_id}] revision {revisions} done")
        verdict = await step(
            f"review-rev{revisions}",
            reviewer(workspace),
            f"Task:\n{task}\n\nSpec:\n{spec}\n\nPrior findings:\n{fixes}\n\n"
            f"Revision report:\n{report}\n\nRe-review the workspace.",
        )
        print(f"[{run_id}] re-review: {verdict.verdict}")

    if verdict.verdict == "pass":
        log["outcome"] = "pass"
        code = 0
    elif revisions >= max_revisions:
        log["outcome"] = "revision-limit"
        code = 2
    else:
        log["outcome"] = "fail"
        code = 1

    log["verdict"] = verdict.model_dump()
    log["revisions"] = revisions
    record(log)
    print(f"\n=== VERDICT: {verdict.verdict.upper()} (revisions: {revisions}) ===")
    print(verdict.summary)
    for f in verdict.findings:
        print(f"  [{f.severity}] {f.file}: {f.description}")
    return code


# --- cli ----------------------------------------------------------------


def doctor() -> int:
    ok = True
    print(f"harness pin: {HARNESS_VERSION}")
    for name, present in [
        ("GLM_API_KEY (zai)", bool(os.environ.get("GLM_API_KEY") or ENV.get("GLM_API_KEY"))),
        ("ALIBABA_TOKEN_PLAN_API_KEY (bailian sk-sp-)", (os.environ.get("ALIBABA_TOKEN_PLAN_API_KEY") or ENV.get("ALIBABA_TOKEN_PLAN_API_KEY", "")).startswith("sk-sp-")),
    ]:
        print(f"  {name}: {'ok' if present else 'MISSING'}")
        ok = ok and present
    print(f"records: {RECORDS} ({RECORDS.exists() and sum(1 for _ in RECORDS.open()) or 0} runs)")
    return 0 if ok else 3


def main() -> int:
    ap = argparse.ArgumentParser(prog="pah")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="plan → code → review pipeline")
    run_p.add_argument("--workspace", required=True, type=Path)
    run_p.add_argument("--task", required=True, help="task file path, or - for stdin")
    run_p.add_argument("--max-revisions", type=int, default=1)
    plan_p = sub.add_parser("plan", help="spec only: read repo, emit implementation spec")
    plan_p.add_argument("--workspace", required=True, type=Path)
    plan_p.add_argument("--task", required=True, help="task file path, or - for stdin")
    plan_p.add_argument("--out", type=Path, default=None, help="spec output path (default <workspace>/../spec-<runid>.md)")
    sub.add_parser("doctor", help="check keys and config")
    show_p = sub.add_parser("show", help="print a run record")
    show_p.add_argument("run_id")
    args = ap.parse_args()

    if args.cmd == "doctor":
        return doctor()

    if args.cmd == "show":
        for line in RECORDS.open():
            rec = json.loads(line)
            if rec["run_id"] == args.run_id:
                print(json.dumps(rec, indent=2, default=str))
                return 0
        print(f"no record {args.run_id}")
        return 3

    task = sys.stdin.read() if args.task == "-" else Path(args.task).read_text()
    workspace = args.workspace.resolve()
    if not workspace.is_dir():
        print(f"workspace {workspace} not found")
        return 3

    if args.cmd == "plan":
        out_path = args.out or workspace.parent / f"spec-{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        try:
            return asyncio.run(plan_only(workspace, task, out_path))
        except Exception as e:  # noqa: BLE001
            print(f"infra error: {type(e).__name__}: {e}", file=sys.stderr)
            return 3
    try:
        return asyncio.run(run_pipeline(workspace, task, args.max_revisions))
    except Exception as e:  # noqa: BLE001
        print(f"infra error: {type(e).__name__}: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
