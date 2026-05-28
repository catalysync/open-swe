"""claude -p subprocess primitives — Max-subscription compute engine (ADR 0002).

Two modes:
- ``claude_text`` — blocking, returns final text. For reasoning nodes
  (planner, reviewer) that must NOT own a tool loop.
- ``start_dev`` / ``read_dev_turn`` — the autonomous turn-loop for the
  developer node, emitting AIMessage(tool_calls) + ToolMessage per turn so
  Studio renders tool calls live.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

_PROCS: dict[str, subprocess.Popen] = {}
_PENDING_EVENTS: dict[str, dict] = {}

_DIM, _RST, _YLW = "\033[2m", "\033[0m", "\033[33m"


def claude_text(prompt: str, *, model: str = "sonnet", timeout: int = 600) -> str:
    """Run claude -p and return its final text. Reasoning-only (no tool loop owned by us)."""
    proc = subprocess.run(
        ["claude", "-p", prompt, "--model", model,
         "--output-format", "text", "--dangerously-skip-permissions"],
        capture_output=True, text=True, timeout=timeout,
    )
    return (proc.stdout or "").strip() or (proc.stderr or "").strip()


def start_dev(prompt: str, *, model: str = "sonnet") -> str:
    """Launch an autonomous claude -p for the developer phase. Returns a proc key."""
    proc = subprocess.Popen(
        ["claude", "-p", prompt, "--model", model,
         "--output-format", "stream-json", "--verbose",
         "--dangerously-skip-permissions"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
    )
    key = uuid.uuid4().hex
    _PROCS[key] = proc
    return key


def _extract_blocks(event: dict, text_parts: list[str], tool_calls: list[dict[str, Any]]) -> None:
    for block in event.get("message", {}).get("content", []):
        btype = block.get("type")
        if btype == "text":
            t = block.get("text", "")
            if t:
                text_parts.append(t)
        elif btype == "tool_use":
            tool_calls.append({
                "name": block.get("name", "tool"),
                "args": block.get("input", {}),
                "id": block.get("id", f"tool-{uuid.uuid4().hex[:8]}"),
            })


def read_dev_turn(key: str, pending_tool_ids: list[str]) -> tuple[list[Any], bool, list[str]]:
    """Read one developer turn. Returns (new_messages, done, new_pending_tool_ids)."""
    proc = _PROCS.get(key)
    if not proc:
        return [AIMessage(content="Developer process ended.")], True, []

    messages: list[Any] = [ToolMessage(content="✓", tool_call_id=t) for t in pending_tool_ids]
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    done = False

    pending = _PENDING_EVENTS.pop(key, None)
    if pending:
        _extract_blocks(pending, text_parts, tool_calls)

    while True:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                done = True
            break
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        etype = event.get("type")
        if etype == "assistant":
            if text_parts or tool_calls:
                _PENDING_EVENTS[key] = event
                break
            _extract_blocks(event, text_parts, tool_calls)
            for tc in tool_calls:
                disp = tc["args"].get("file_path") or tc["args"].get("command", "")
                if not isinstance(disp, str):
                    disp = json.dumps(tc["args"])[:100]
                sys.stderr.write(f"  {_YLW}⚡ {tc['name']}{_RST} {_DIM}{str(disp)[:100]}{_RST}\n")
                sys.stderr.flush()
        elif etype == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                t = delta.get("text", "")
                if t:
                    text_parts.append(t)
        elif etype == "result":
            rt = event.get("result", "")
            if rt and not text_parts:
                text_parts.append(rt)
            done = True
            break

    content = "".join(text_parts)
    if content or tool_calls:
        messages.append(AIMessage(content=content, tool_calls=tool_calls))

    new_pending = [tc["id"] for tc in tool_calls]
    if done:
        for tid in new_pending:
            messages.append(ToolMessage(content="✓", tool_call_id=tid))
        new_pending = []
        proc.wait()
        _PROCS.pop(key, None)
        _PENDING_EVENTS.pop(key, None)

    return messages, done, new_pending
