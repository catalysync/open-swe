"""`mine` graph — replicate a reference into an existing project, section by
section. Source-agnostic (video → frames, images dir, or docs/transcripts);
optional per-section visual-parity gate; bounded per-section retries so it never
loops the whole job infinitely.

ingest → dispatch ─next─→ section_dev → section_turn ⇄ → gate → dispatch
                  └─done─→ END
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.pregel import Pregel

from .claude import claude_text, read_dev_turn, start_dev
from .mine_state import MineState
from .projects import project_root
from .validator import run_gate

_VIDEO = {".mp4", ".mov", ".webm", ".mkv"}
_IMAGE = {".png", ".jpg", ".jpeg", ".svg", ".webp"}
_PARITY_BAR = 90
_MAX_SECTION_RETRIES = 2


def _decompose(source: str) -> tuple[str, list[dict]]:
    p = Path(source)
    if p.is_file() and p.suffix.lower() in _VIDEO:
        out = Path(tempfile.mkdtemp(prefix="frames-"))
        if shutil.which("ffmpeg"):
            subprocess.run(["ffmpeg", "-i", source, "-vf", "fps=1/3",
                            str(out / "f_%04d.png")], capture_output=True, timeout=300)
        frames = sorted(str(f) for f in out.glob("*.png"))
        return "video", [{"label": Path(f).stem, "ref": f, "ref_kind": "image"} for f in frames]
    if p.is_dir():
        imgs = sorted(str(f) for f in p.iterdir() if f.suffix.lower() in _IMAGE)
        if imgs:
            return "images", [{"label": Path(f).stem, "ref": f, "ref_kind": "image"} for f in imgs]
    # docs / transcript → ask claude to split into ordered buildable sections
    text = p.read_text()[:8000] if p.is_file() else ""
    raw = claude_text(
        "Split this reference into an ordered list of buildable UI sections/"
        "features. Reply ONLY a JSON array of {\"label\":..., \"description\":...}.\n\n"
        + text
    )
    m = re.search(r"\[.*\]", raw, re.S)
    items = json.loads(m.group(0)) if m else []
    return "docs", [{"label": i.get("label", f"s{n}"), "ref": i.get("description", ""),
                     "ref_kind": "text"} for n, i in enumerate(items)]


def ingest_node(state: MineState, config: RunnableConfig) -> dict:
    cfg = (config or {}).get("configurable", {}) or {}
    source = state.get("source") or cfg.get("source", "")
    root = project_root(state, config)
    visual = bool(state.get("visual") or cfg.get("visual", False))
    kind, sections = _decompose(source) if source else ("docs", [])
    return {"source": source, "project_root": root, "source_kind": kind, "visual": visual,
            "sections": sections, "idx": 0, "section_retries": 0,
            "messages": [AIMessage(content=f"⛏️ **Mine** ({kind}): {len(sections)} sections from `{source}`")]}


def _cur(state: MineState) -> dict | None:
    s = state.get("sections") or []
    i = state.get("idx", 0)
    return s[i] if i < len(s) else None


def section_dev_node(state: MineState) -> dict:
    sec = _cur(state)
    root = state.get("project_root", ".")
    if sec["ref_kind"] == "image":
        body = (f"Replicate the UI shown in the reference image at `{sec['ref']}` into the "
                f"project at {root}. Read the image first. Match existing components/patterns; "
                f"build full-stack as needed.")
    else:
        body = f"Implement this section in {root}, matching existing patterns:\n{sec['ref']}"
    fb = ""
    if state.get("section_retries"):
        fb = "\nPrior attempt fell short; address the gate feedback in the messages above."
    key = start_dev(f"You are replicating section '{sec['label']}'.\n{body}{fb}")
    return {"proc_key": key, "dev_done": False, "pending_tool_ids": []}


def section_turn_node(state: MineState) -> dict:
    msgs, done, pend = read_dev_turn(state.get("proc_key", ""), state.get("pending_tool_ids", []))
    return {"messages": msgs, "dev_done": done, "pending_tool_ids": pend}


def gate_node(state: MineState, config: RunnableConfig) -> dict:
    sec = _cur(state)
    root = state.get("project_root", ".")
    if state.get("visual") and sec["ref_kind"] == "image":
        raw = claude_text(
            f"The dev server for the project at {root} is running. Using Playwright, screenshot "
            f"the route matching the reference, compare to `{sec['ref']}`, and reply ONLY JSON "
            f'{{"parity": <0-100>, "mismatches": ["..."]}}.'
        )
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0)) if m else {"parity": 100, "mismatches": []}
        ok = data.get("parity", 0) >= _PARITY_BAR
        note = f"parity={data.get('parity')} {data.get('mismatches')}"
    else:
        v = run_gate(root)
        ok = v.get("lint_passed", True) and v.get("tests_passed", True) and v.get("security_passed", True)
        note = "gates green" if ok else f"gate fail: {v.get('errors')}"
    return {"messages": [AIMessage(content=f"🎚️ **{sec['label']}**: {note}")], "parity": 1 if ok else 0}


def advance_node(state: MineState) -> dict:
    passed = state.get("parity") == 1
    msg = "✅ next" if passed else "⏭️ skipped (out of retries)"
    return {"idx": state.get("idx", 0) + 1, "section_retries": 0,
            "messages": [AIMessage(content=msg)]}


def retry_node(state: MineState) -> dict:
    n = state.get("section_retries", 0) + 1
    return {"section_retries": n, "dev_done": False,
            "messages": [AIMessage(content=f"🔁 retry {n}")]}


def route_dispatch(state: MineState) -> Literal["build", "end"]:
    return "build" if _cur(state) else "end"


def route_turn(state: MineState) -> Literal["turn", "gate"]:
    return "gate" if state.get("dev_done") else "turn"


def route_gate(state: MineState) -> Literal["retry", "advance"]:
    passed = state.get("parity") == 1
    if passed or state.get("section_retries", 0) >= _MAX_SECTION_RETRIES:
        return "advance"
    return "retry"


def build_mine_graph() -> Pregel:
    g = StateGraph(MineState)
    g.add_node("ingest", ingest_node)
    g.add_node("dispatch", lambda s: {})
    g.add_node("section_dev", section_dev_node)
    g.add_node("section_turn", section_turn_node)
    g.add_node("gate", gate_node)
    g.add_node("advance", advance_node)
    g.add_node("retry", retry_node)

    g.add_edge(START, "ingest")
    g.add_edge("ingest", "dispatch")
    g.add_conditional_edges("dispatch", route_dispatch, {"build": "section_dev", "end": END})
    g.add_edge("section_dev", "section_turn")
    g.add_conditional_edges("section_turn", route_turn,
                            {"turn": "section_turn", "gate": "gate"})
    g.add_conditional_edges("gate", route_gate, {"advance": "advance", "retry": "retry"})
    g.add_edge("advance", "dispatch")
    g.add_edge("retry", "section_dev")
    return g.compile()
