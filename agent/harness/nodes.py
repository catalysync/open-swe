"""ADR nodes — planner, developer (turn-loop), reviewer, validator, aggregator.

Each node appends a human-readable AIMessage to `messages` so Studio renders
the full pipeline as a conversation, while typed fields drive routing.
"""

from __future__ import annotations

import subprocess

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from . import prompts
from .claude import claude_text, read_dev_turn, start_dev
from .contracts import ReviewResult, parse_or_repair
from .projects import (
    load_manifest, load_rules, load_skills, project_root, recent_memory, resolve_project,
)
from .state import HarnessState
from .validator import run_gate

MAX_RETRIES = 3  # ADR 0006/0012: escalate after 3 retries


_project_root = project_root  # resolution order: state → config → env → workspace


def _task_from_messages(state: HarnessState) -> str:
    for msg in state["messages"]:
        if isinstance(msg, HumanMessage):
            return msg.content if isinstance(msg.content, str) else str(msg.content)
        if getattr(msg, "type", None) == "human":
            return msg.content if isinstance(msg.content, str) else str(msg.content)
    return ""


def supervisor_node(state: HarnessState, config: RunnableConfig) -> dict:
    """ADR 0005 supervisor — triage (question vs build), resolve target repo, dispatch."""
    task = _task_from_messages(state)
    target = resolve_project(task) or _project_root(state, config)

    verdict = claude_text(
        "Classify the user's message as exactly one word: QUESTION (they want an "
        "answer/explanation, no code changes) or BUILD (they want code written or "
        f"changed). Reply with only the word.\n\nMessage:\n{task}"
    ).strip().upper()

    if "QUESTION" in verdict and "BUILD" not in verdict:
        answer = claude_text(
            f"Answer the user's question about the codebase at {target}. "
            f"Explore read-only as needed; do NOT edit files.\n\nQuestion:\n{task}"
        )
        return {"task": task, "project_root": target, "status": "done",
                "messages": [AIMessage(content=answer)]}

    framing = claude_text(
        "You are the SUPERVISOR of a software-engineering harness. In 2-3 lines, "
        "restate the build task crisply and note the single most important "
        f"constraint. Do not plan or edit anything.\n\nTask:\n{task}"
    )
    return {
        "task": task,
        "project_root": target,
        "status": "planning",
        "retry_count": 0,
        "messages": [AIMessage(content=f"🧭 **Supervisor** (target: `{target}`)\n\n{framing}")],
    }


def planner_node(state: HarnessState, config: RunnableConfig) -> dict:
    task = state.get("task") or _task_from_messages(state)
    root = _project_root(state, config)
    mem = recent_memory(root)
    task_with_mem = task if not mem else f"{task}\n\nRecent successful builds here:\n{mem}"
    rules = load_rules(root)
    rules_block = f"\nProject house rules:\n{rules}\n" if rules else ""
    plan = claude_text(prompts.PLANNER.format(
        task=task_with_mem, project_root=root, rules=rules_block))
    return {
        "task": task,
        "plan": plan,
        "status": "developing",
        "retry_count": state.get("retry_count", 0),
        "messages": [AIMessage(content=f"📋 **Plan**\n\n{plan}")],
    }


def developer_node(state: HarnessState, config: RunnableConfig) -> dict:
    """Launch claude -p for the dev phase. Streaming happens in developer_turn."""
    root = _project_root(state, config)
    feedback = ""
    review = state.get("review") or {}
    validation = state.get("validation") or {}
    if review.get("status") == "NEEDS_REVISION" or (validation and not _val_ok(validation)):
        notes = list(dict.fromkeys(review.get("findings", []) + validation.get("errors", [])))
        feedback = "\nReviewer/validator feedback to address:\n" + "\n".join(
            f"- {n}" for n in notes
        ) + "\n"
    skills = load_skills(root)
    skills_block = f"\nSkill templates to follow EXACTLY:\n{skills}\n" if skills else ""
    rules = load_rules(root)
    rules_block = f"\nProject house rules (AGENTS.md/CLAUDE.md):\n{rules}\n" if rules else ""
    manifest = load_manifest(root)
    manifest_block = f"  Manifest — prefer these deps:\n  {manifest}\n" if manifest else ""
    prompt = prompts.DEVELOPER.format(
        task=state.get("task", ""), plan=state.get("plan", ""), manifest=manifest_block,
        rules=rules_block, skills=skills_block, feedback=feedback, project_root=root,
    )
    key = start_dev(prompt)
    return {"proc_key": key, "dev_done": False, "pending_tool_ids": [], "status": "developing"}


def developer_turn(state: HarnessState) -> dict:
    msgs, done, new_pending = read_dev_turn(
        state.get("proc_key", ""), state.get("pending_tool_ids", [])
    )
    return {"messages": msgs, "dev_done": done, "pending_tool_ids": new_pending}


def reviewer_node(state: HarnessState, config: RunnableConfig) -> dict:
    root = _project_root(state, config)
    diff = _git_diff(root)
    if not diff.strip():
        review = {"status": "APPROVED", "findings": ["no diff produced"]}
        return {"review": review, "status": "validating",
                "messages": [AIMessage(content="🔍 **Review**: APPROVED (no changes)")]}
    raw = claude_text(prompts.REVIEWER.format(task=state.get("task", ""), diff=diff[:12000]))
    result = parse_or_repair(
        raw, ReviewResult,
        default=ReviewResult(status="APPROVED", findings=["reviewer output unparseable"]),
    )
    body = "\n".join(f"- {f}" for f in result.findings) or "- none"
    return {"review": result.model_dump(), "status": "validating",
            "messages": [AIMessage(content=f"🔍 **Review**: {result.status}\n{body}")]}


def validator_node(state: HarnessState, config: RunnableConfig) -> dict:
    root = _project_root(state, config)
    validation = run_gate(root)
    summary = (
        f"lint={'pass' if validation['lint_passed'] else 'FAIL'} "
        f"tests={'pass' if validation['tests_passed'] else 'FAIL'} "
        f"{'(skipped — no toolchain)' if not validation.get('ran') else ''}"
    )
    return {"validation": validation, "status": "aggregating",
            "messages": [AIMessage(content=f"✅ **Validation**: {summary}")]}


def aggregator_node(state: HarnessState) -> dict:
    review = state.get("review") or {}
    validation = state.get("validation") or {}
    retry = state.get("retry_count", 0)
    review_ok = review.get("status") == "APPROVED"
    val_ok = _val_ok(validation)

    crit = review.get("severity") == "critical" and not review_ok
    if review_ok and val_ok:
        return {"status": "done",
                "messages": [AIMessage(content="🎉 **Done** — review approved, gates green.")]}
    if crit or retry >= MAX_RETRIES:
        un = list(dict.fromkeys(review.get("findings", []) + validation.get("errors", [])))
        items = "\n".join(f"- {u}" for u in un) or "- (none captured)"
        why = "critical finding — needs a human" if crit else f"{retry} retries"
        return {"status": "escalated",
                "messages": [AIMessage(content=f"⚠️ **Escalated** ({why}).\n{items}")]}
    return {"status": "developing", "retry_count": retry + 1, "dev_done": False,
            "messages": [AIMessage(content=f"🔁 **Retry {retry + 1}** — sending feedback back to developer.")]}


def _val_ok(validation: dict) -> bool:
    keys = ("lint_passed", "structural_passed", "security_passed", "tests_passed")
    return all(validation.get(k, True) for k in keys)


def _git_diff(root: str) -> str:
    try:
        r = subprocess.run(["git", "diff", "HEAD"], cwd=root,
                           capture_output=True, text=True, timeout=30)
        return r.stdout
    except Exception:  # noqa: BLE001
        return ""


