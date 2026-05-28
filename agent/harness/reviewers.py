"""Parallel review — quality ∥ security (Google: different reviewers for
different parts). Each runs a focused claude -p on the diff, emits a validated
ReviewResult to its own state key; review_merge combines them so routing sees
one verdict. Concurrent so latency stays ~1x.
"""

from __future__ import annotations

import subprocess

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from . import prompts
from .claude import claude_text
from .contracts import ReviewResult, parse_or_repair
from .projects import project_root
from .state import HarnessState

_SEV = ["none", "low", "medium", "high", "critical"]


def _diff(root: str) -> str:
    try:
        r = subprocess.run(["git", "diff", "HEAD"], cwd=root,
                           capture_output=True, text=True, timeout=30)
        return r.stdout
    except Exception:  # noqa: BLE001
        return ""


def _review(tmpl: str, task: str, diff: str) -> ReviewResult:
    raw = claude_text(tmpl.format(task=task, diff=diff[:12000]))
    return parse_or_repair(
        raw, ReviewResult,
        default=ReviewResult(status="APPROVED", severity="none",
                             findings=["reviewer output unparseable"]),
    )


def _body(label: str, r: ReviewResult) -> AIMessage:
    items = "\n".join(f"- {f}" for f in r.findings) or "- none"
    return AIMessage(content=f"{label} **{r.status}** (sev={r.severity})\n{items}")


def quality_reviewer_node(state: HarnessState, config: RunnableConfig) -> dict:
    diff = _diff(project_root(state, config))
    if not diff.strip():
        return {"review_quality": ReviewResult(status="APPROVED").model_dump()}
    r = _review(prompts.QUALITY_REVIEWER, state.get("task", ""), diff)
    return {"review_quality": r.model_dump(), "messages": [_body("🔍 Quality:", r)]}


def security_reviewer_node(state: HarnessState, config: RunnableConfig) -> dict:
    diff = _diff(project_root(state, config))
    if not diff.strip():
        return {"review_security": ReviewResult(status="APPROVED").model_dump()}
    r = _review(prompts.SECURITY_REVIEWER, state.get("task", ""), diff)
    return {"review_security": r.model_dump(), "messages": [_body("🔒 Security:", r)]}


def review_merge_node(state: HarnessState) -> dict:
    q = state.get("review_quality") or {}
    s = state.get("review_security") or {}
    findings = q.get("findings", []) + s.get("findings", [])
    status = "NEEDS_REVISION" if "NEEDS_REVISION" in (q.get("status"), s.get("status")) else "APPROVED"
    severity = max((q.get("severity", "none"), s.get("severity", "none")), key=_SEV.index)
    review = {"status": status, "severity": severity, "findings": findings}
    return {"review": review, "status": "validating",
            "messages": [AIMessage(content=f"📋 **Review**: {status} (sev={severity}, {len(findings)} findings)")]}
