"""Headless runner — drive the harness from the terminal, no Studio.

Streams each node + each AIMessage/ToolMessage as it lands, so you watch the
supervisor → planner → developer(⚡ tools live) → reviewers → validator →
aggregator pipeline as a text stream (the prototype experience). The developer
node already writes its live `⚡ tool` lines to stderr from claude.py; this adds
the per-node + per-message narration on top.

    python -m agent.harness.cli "add a subtract(a,b) to calc.py with a test" \
        --root /path/to/repo

Defaults: project_root = $HARNESS_PROJECT_ROOT or cwd; hitl off (unattended).
"""

from __future__ import annotations

import argparse
import sys

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from . import cost
from .graph import build_harness_graph

_CY, _GR, _DIM, _RST, _BOLD = "\033[36m", "\033[32m", "\033[2m", "\033[0m", "\033[1m"


def _render(node: str, delta: dict) -> None:
    sys.stdout.write(f"\n{_BOLD}{_CY}▸ {node}{_RST}\n")
    for msg in delta.get("messages", []) or []:
        if isinstance(msg, ToolMessage):
            continue  # the ⚡ tool lines already streamed to stderr live
        if isinstance(msg, AIMessage):
            for tc in msg.tool_calls or []:
                arg = tc["args"].get("file_path") or tc["args"].get("command", "")
                sys.stdout.write(f"  {_DIM}⚡ {tc['name']} {str(arg)[:80]}{_RST}\n")
            text = msg.content if isinstance(msg.content, str) else ""
            if text.strip():
                sys.stdout.write(f"{text.strip()}\n")
    sys.stdout.flush()


def run(task: str, root: str | None, hitl: bool) -> int:
    graph = build_harness_graph(hitl=hitl)
    cfg: dict = {"recursion_limit": 9999}
    if root:
        cfg["configurable"] = {"project_root": root}
    cost.reset()
    final: dict = {}
    for chunk in graph.stream({"messages": [HumanMessage(content=task)]},
                              config=cfg, stream_mode="updates"):
        for node, delta in chunk.items():
            if isinstance(delta, dict):
                _render(node, delta)
                final = {**final, **delta}

    status = final.get("status", "?")
    review = (final.get("review") or {}).get("status", "?")
    c = cost.snapshot()
    color = _GR if status == "done" else "\033[33m"
    sys.stdout.write(
        f"\n{_BOLD}{color}■ {status}{_RST}  review={review}  "
        f"retries={final.get('retry_count', 0)}  "
        f"${c['usd']:.3f}  {c['input_tokens'] + c['output_tokens']}tok\n"
    )
    return 0 if status == "done" else 1


def main() -> None:
    ap = argparse.ArgumentParser(prog="harness", description="Run the harness headless.")
    ap.add_argument("task", help="what to build")
    ap.add_argument("--root", default=None, help="target repo (default: cwd / $HARNESS_PROJECT_ROOT)")
    ap.add_argument("--hitl", action="store_true", help="pause before the curator writes a template")
    args = ap.parse_args()
    raise SystemExit(run(args.task, args.root, args.hitl))


if __name__ == "__main__":
    main()
