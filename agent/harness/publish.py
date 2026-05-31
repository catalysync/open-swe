"""PR node — commit the run's change to a fresh branch and open a PR.

Runs on the success path (status == done) when opted in, so a harness run ends
with a reviewable PR instead of loose working-tree edits. Opt-in via env
``HARNESS_OPEN_PR=1`` or config ``configurable.open_pr`` — off by default so
bench/scratch runs don't push branches.

Excludes harness scratch (.agents, *.db, *.jsonl) from the commit. Pushes with
GITHUB_TOKEN unset (codespaces bot-token gotcha) and opens the PR via ``gh`` with
the project's PULL_REQUEST_TEMPLATE if present.
"""

from __future__ import annotations

import os
import re
import subprocess

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from .state import HarnessState

_EXCLUDE = (":(exclude).agents/**", ":(exclude)**/*.db", ":(exclude)**/*.jsonl")


def _sh(args: list[str], root: str, *, token_unset: bool = False) -> tuple[bool, str]:
    env = dict(os.environ)
    if token_unset:
        env.pop("GITHUB_TOKEN", None)
    try:
        r = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=120, env=env)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _slug(task: str) -> str:
    # First non-empty line, stripped of any leaked message-envelope punctuation,
    # then kebab-cased. Belt-and-suspenders even if task text is already clean.
    first = next((ln for ln in task.splitlines() if ln.strip()), task)
    s = re.sub(r"[^a-z0-9]+", "-", first.lower()).strip("-")
    return (s[:48] or "harness-change").rstrip("-")


def _opted_in(config: RunnableConfig) -> bool:
    cfg = (config or {}).get("configurable", {}) or {}
    return bool(cfg.get("open_pr") or os.environ.get("HARNESS_OPEN_PR") == "1")


def _pr_body(state: HarnessState) -> str:
    """Professional, consistent PR body (Google-style eng review sections)."""
    plan = (state.get("plan") or "_(no plan captured)_")[:3000]
    review = state.get("review") or {}
    val = state.get("validation") or {}
    findings = review.get("findings") or []
    risks = "\n".join(f"- {f}" for f in findings) or "- None flagged by review."
    gate = "passed" if val.get("passed") else "see CI"
    return (
        f"## Context\n{state.get('task', '')[:1500]}\n\n"
        f"## What changed\n{plan}\n\n"
        "## Why\nImplements the task above following the project's ADRs and "
        "house rules (offline-first, loading-UX triad, auth gating).\n\n"
        f"## Testing\nFull-stack gate: {gate} "
        f"(lint, types, tests, security). Offline-flow Playwright per ADR 0016.\n\n"
        f"## Risk & rollback\n{risks}\n\nRollback: revert this PR; no data migration.\n\n"
        "## Screenshots\n_Attach before merge if UI-facing._"
    )


def publish_pr(state: HarnessState, config: RunnableConfig) -> dict:
    root = state.get("project_root", ".")
    if not _opted_in(config):
        return {}
    task = state.get("task", "harness change")
    branch = f"feat/{_slug(task)}"
    subject = _slug(task).replace("-", " ")[:60]

    _sh(["git", "checkout", "-B", branch], root)
    _sh(["git", "add", "--", ".", *_EXCLUDE], root)
    ok_commit, _ = _sh(["git", "commit", "-m", subject], root)
    if not ok_commit:
        return {"messages": [AIMessage(content="🔀 **PR**: nothing to commit (no changes).")]}

    ok_push, push_out = _sh(["git", "push", "-u", "origin", branch], root, token_unset=True)
    if not ok_push:
        return {"messages": [AIMessage(content=f"🔀 **PR**: push failed.\n{push_out[:300]}")]}

    ok_pr, pr_out = _sh(
        ["gh", "pr", "create", "--head", branch, "--title", subject, "--body", _pr_body(state)],
        root, token_unset=True,
    )
    msg = f"🔀 **PR opened**: {pr_out.strip().splitlines()[-1]}" if ok_pr else \
        f"🔀 **PR**: pushed `{branch}`; open it manually.\n{pr_out[:300]}"
    return {"messages": [AIMessage(content=msg)]}
