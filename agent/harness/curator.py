"""Curator — learns templates from successful builds (ADR 0007), gated by a
human approval interrupt (ADR 0008).

After a build reaches Done, ``curator_propose`` checks whether the developer
introduced a NEW reusable pattern not already in the skill templates. If so it
drafts a skill-template update and the graph interrupts (``interrupt_before``)
so the human approves/edits in Studio before ``curator_apply`` writes it.
"""

from __future__ import annotations

import subprocess
from typing import Literal

from langchain_core.messages import AIMessage

from .claude import claude_text
from .projects import append_memory, write_skill
from .state import HarnessState

_SENTINEL = "NONE"


def _diff(root: str) -> str:
    try:
        r = subprocess.run(["git", "diff", "HEAD"], cwd=root,
                           capture_output=True, text=True, timeout=30)
        return r.stdout
    except Exception:  # noqa: BLE001
        return ""


def curator_propose(state: HarnessState) -> dict:
    root = state.get("project_root", ".")
    diff = _diff(root)
    summary = ""
    for m in reversed(state["messages"]):
        if getattr(m, "type", None) == "ai" and isinstance(m.content, str) and m.content.strip():
            summary = m.content[:200]
            break
    append_memory(root, {"task": state.get("task", ""), "summary": summary,
                         "stack": (state.get("validation") or {}).get("stack", "")})

    if not diff.strip():
        return {"proposal": None}

    raw = claude_text(
        "You curate reusable skill templates for a codegen harness. Given this "
        "diff, decide if it demonstrates a NEW reusable pattern not yet captured "
        "as a template. If yes, reply with:\nNAME: <kebab-skill-name>\n<full "
        f"markdown template>\nIf no, reply with exactly: {_SENTINEL}\n\nDiff:\n{diff[:12000]}"
    )
    if _SENTINEL in raw and "NAME:" not in raw:
        return {"proposal": None,
                "messages": [AIMessage(content="🧩 **Curator**: no new template pattern.")]}

    name = "learned-pattern"
    body = raw
    if raw.strip().startswith("NAME:"):
        first, _, rest = raw.partition("\n")
        name = first.split("NAME:", 1)[1].strip() or name
        body = rest.strip()
    return {
        "proposal": {"name": name, "content": body},
        "messages": [AIMessage(content=(
            f"🧩 **Curator** proposes a new skill template `{name}` (approve to save).\n\n"
            f"```md\n{body[:1500]}\n```\n\n_Resume to approve; edit `proposal` in state to "
            "change; clear it to skip._"
        ))],
    }


def curator_apply(state: HarnessState) -> dict:
    proposal = state.get("proposal")
    root = state.get("project_root", ".")
    if not proposal:
        return {"messages": [AIMessage(content="🧩 **Curator**: skipped (no approved template).")]}
    path = write_skill(root, proposal["name"], proposal["content"])
    return {"proposal": None,
            "messages": [AIMessage(content=f"💾 **Curator**: saved template → `{path}`")]}


def route_curator(state: HarnessState) -> Literal["apply", "end"]:
    return "apply" if state.get("proposal") else "end"
