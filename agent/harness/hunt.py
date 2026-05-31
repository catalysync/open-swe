"""`hunt` graph — adversarial bug-finding + fixing with sequenced PRs.

A discovery pass mines the codebase for bugs, missing test coverage (aim: cover
the cyclomatic branches), and small feature gaps. It cuts a per-task integration
branch `hunt/<slug>` off HEAD, then fixes each finding on its own sequenced
branch `hunt/<slug>/NN-<fix>` off the integration branch, gates it, and opens a
PR into the integration branch — so the reviewer can follow fixes in order.

scan → dispatch ─next─→ fix_dev → fix_turn ⇄ → gate → {pr → advance | retry}
                └─done─→ END

DANGER: fixes run with only the diff + repo context the dev gathers; a fix can
regress an existing feature it lacks context for. Bounded retries + per-fix gate
+ per-fix PR (never auto-merged) keep the blast radius reviewable, not zero.

Opt-in PRs via env HARNESS_OPEN_PR=1 / config open_pr (same as the build graph).
"""

from __future__ import annotations

import json
import re
from typing import Literal

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.pregel import Pregel

from .claude import claude_text, read_dev_turn, start_dev
from .hunt_git import commit_and_pr, complexity_hotspots, opted_in, sh, slug
from .hunt_state import HuntState
from .projects import load_rules, project_root
from .text import content_text
from .validator import run_gate

_MAX_FIX_RETRIES = 2
_MAX_BUGS = 12  # cap a single hunt so it stays reviewable; loop again for more


def _task(state: HuntState) -> str:
    if state.get("task"):
        return state["task"]
    for m in state["messages"]:
        if getattr(m, "type", None) == "human":
            return content_text(m.content)
    return "hunt for bugs"


def _fix_branch(state: HuntState, bug: dict) -> tuple[int, str]:
    n = state.get("idx", 0) + 1
    return n, f"{state['task_branch']}/{n:02d}-{slug(bug['title'])}"


def scan_node(state: HuntState, config: RunnableConfig) -> dict:
    """Discovery: mine bugs + coverage gaps + small feature gaps, cut the
    integration branch off HEAD."""
    task = _task(state)
    root = project_root(state, config)
    base_ref = sh(["git", "rev-parse", "HEAD"], root)[1] or "HEAD"
    task_branch = f"hunt/{slug(task)}"
    sh(["git", "checkout", "-B", task_branch], root)

    rules = load_rules(root)
    hot = complexity_hotspots(root)
    hot_block = (f"\n\nHigh-complexity functions (radon cc, rank ≥C) — prioritise "
                 f"covering their branches:\n{hot}" if hot else "")
    raw = claude_text(
        "You are an adversarial bug hunter on this codebase at "
        f"{root}. Read the code (read-only). Find: real correctness bugs; "
        "functions whose cyclomatic branches lack test coverage; and small "
        "feature gaps that are really latent bugs. Prioritise high-severity, "
        "high-confidence, isolated issues. Reply ONLY a JSON array (max "
        f"{_MAX_BUGS}) of objects: "
        '{"title":..., "file":..., "kind":"bug|coverage|feature", '
        '"severity":"low|medium|high|critical", "fix":"<what to change + the '
        'test that proves it>"}.\n\n'
        f"Focus / scope from the operator:\n{task}{hot_block}\n\n"
        f"Project house rules:\n{rules[:3000]}"
    )
    m = re.search(r"\[.*\]", raw, re.S)
    bugs = json.loads(m.group(0)) if m else []
    bugs = [b for b in bugs if isinstance(b, dict) and b.get("title")][:_MAX_BUGS]
    bugs.sort(key=lambda b: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(b.get("severity"), 4))
    return {
        "task": task, "project_root": root, "base_ref": base_ref,
        "task_branch": task_branch, "bugs": bugs, "idx": 0, "bug_retries": 0,
        "messages": [AIMessage(content=f"🔭 **Hunt** on `{task_branch}`: {len(bugs)} findings.")],
    }


def _cur(state: HuntState) -> dict | None:
    bugs = state.get("bugs") or []
    i = state.get("idx", 0)
    return bugs[i] if i < len(bugs) else None


def fix_dev_node(state: HuntState) -> dict:
    bug = _cur(state)
    root = state.get("project_root", ".")
    _, fix_branch = _fix_branch(state, bug)
    # branch each fix off the integration branch so PRs stack in sequence
    sh(["git", "checkout", state["task_branch"]], root)
    sh(["git", "checkout", "-B", fix_branch], root)
    fb = "\nPrior attempt failed the gate; read the feedback above and fix it." if state.get("bug_retries") else ""
    key = start_dev(
        f"Fix this finding in the project at {root}. PRESERVE all existing "
        "behaviour — change only what the fix needs, add a test that proves it, "
        "do not break unrelated features.\n\n"
        f"[{bug.get('severity')}] {bug.get('kind')}: {bug['title']}\n"
        f"File: {bug.get('file')}\nFix: {bug.get('fix')}{fb}"
    )
    return {"proc_key": key, "dev_done": False, "pending_tool_ids": []}


def fix_turn_node(state: HuntState) -> dict:
    msgs, done, pend = read_dev_turn(state.get("proc_key", ""), state.get("pending_tool_ids", []))
    return {"messages": msgs, "dev_done": done, "pending_tool_ids": pend}


def gate_node(state: HuntState) -> dict:
    root = state.get("project_root", ".")
    v = run_gate(root)
    ok = v.get("passed", False) if "passed" in v else (
        v.get("lint_passed", True) and v.get("tests_passed", True)
        and v.get("types_passed", True) and v.get("security_passed", True))
    note = "gate green" if ok else f"gate FAIL: {str(v.get('errors'))[:300]}"
    return {"gate_ok": 1 if ok else 0,
            "messages": [AIMessage(content=f"🎚️ {note}")]}


def pr_node(state: HuntState, config: RunnableConfig) -> dict:
    bug = _cur(state)
    root = state.get("project_root", ".")
    n, fix_branch = _fix_branch(state, bug)
    subject = f"{n:02d} {bug['title']}"[:60]
    body = (f"## Context\nHunt finding #{n} on `{state['task_branch']}`.\n\n"
            f"## What changed\n[{bug.get('severity')}] {bug.get('kind')}: {bug['title']}\n\n"
            f"{bug.get('fix', '')}\n\n## Testing\nFull-stack gate green; added a "
            "test proving the fix.\n\n## Risk & rollback\nScoped fix; may lack full "
            "feature context — review before merge. Revert this PR to roll back.")
    res = commit_and_pr(root, fix_branch, state["task_branch"], subject, body,
                        do_pr=opted_in(config))
    return {"messages": [AIMessage(content=f"🔀 #{n}: {res}")]}


def advance_node(state: HuntState) -> dict:
    return {"idx": state.get("idx", 0) + 1, "bug_retries": 0, "gate_ok": 0,
            "messages": [AIMessage(content="➡️ next finding")]}


def retry_node(state: HuntState) -> dict:
    n = state.get("bug_retries", 0) + 1
    return {"bug_retries": n, "dev_done": False,
            "messages": [AIMessage(content=f"🔁 retry {n}")]}


def route_dispatch(state: HuntState) -> Literal["fix", "end"]:
    return "fix" if _cur(state) else "end"


def route_turn(state: HuntState) -> Literal["turn", "gate"]:
    return "gate" if state.get("dev_done") else "turn"


def route_gate(state: HuntState) -> Literal["pr", "retry", "skip"]:
    if state.get("gate_ok") == 1:
        return "pr"
    if state.get("bug_retries", 0) >= _MAX_FIX_RETRIES:
        return "skip"  # out of retries → drop this finding, keep hunting
    return "retry"


def build_hunt_graph() -> Pregel:
    g = StateGraph(HuntState)
    g.add_node("scan", scan_node)
    g.add_node("dispatch", lambda s: {})
    g.add_node("fix_dev", fix_dev_node)
    g.add_node("fix_turn", fix_turn_node)
    g.add_node("gate", gate_node)
    g.add_node("pr", pr_node)
    g.add_node("advance", advance_node)
    g.add_node("retry", retry_node)
    g.add_node("skip", advance_node)  # same effect: move on, reset counters

    g.add_edge(START, "scan")
    g.add_edge("scan", "dispatch")
    g.add_conditional_edges("dispatch", route_dispatch, {"fix": "fix_dev", "end": END})
    g.add_edge("fix_dev", "fix_turn")
    g.add_conditional_edges("fix_turn", route_turn, {"turn": "fix_turn", "gate": "gate"})
    g.add_conditional_edges("gate", route_gate, {"pr": "pr", "retry": "retry", "skip": "skip"})
    g.add_edge("pr", "advance")
    g.add_edge("advance", "dispatch")
    g.add_edge("skip", "dispatch")
    g.add_edge("retry", "fix_dev")
    return g.compile()
