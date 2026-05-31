"""Claude CLI agent — streams tool calls as LangChain messages.

Each Claude turn becomes an AIMessage (with tool_calls) + ToolMessages,
so the Open SWE dashboard renders tool calls, diffs, and text live.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.pregel import Pregel
from typing_extensions import Annotated, NotRequired, TypedDict

_PROCS: dict[str, subprocess.Popen] = {}
_PENDING_EVENTS: dict[str, dict] = {}


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    proc_key: NotRequired[str]
    done: NotRequired[bool]
    pending_tool_ids: NotRequired[list[str]]


def _start_claude(state: AgentState) -> dict:
    messages = state["messages"]
    last_msg = None
    system_parts: list[str] = []

    for msg in messages:
        if isinstance(msg, HumanMessage):
            last_msg = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
        elif hasattr(msg, "type") and msg.type == "human":
            last_msg = msg.content if isinstance(msg.content, str) else str(msg.content)
        elif hasattr(msg, "type") and msg.type == "system":
            system_parts.append(msg.content if isinstance(msg.content, str) else str(msg.content))

    if not last_msg:
        return {"messages": [AIMessage(content="No task provided.")], "done": True, "pending_tool_ids": []}

    prompt = last_msg
    if system_parts:
        prompt = "\n\n".join(system_parts) + "\n\n" + prompt

    proc = subprocess.Popen(
        ["claude", "-p", prompt, "--model", "sonnet",
         "--output-format", "stream-json", "--verbose",
         "--dangerously-skip-permissions"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    key = uuid.uuid4().hex
    _PROCS[key] = proc
    return {"proc_key": key, "done": False, "pending_tool_ids": []}


def _read_turn(state: AgentState) -> dict:
    key = state.get("proc_key", "")
    proc = _PROCS.get(key)
    if not proc:
        return {"done": True, "messages": [AIMessage(content="Process ended.")], "pending_tool_ids": []}

    messages: list[Any] = []

    for tid in state.get("pending_tool_ids", []):
        messages.append(ToolMessage(content="✓", tool_call_id=tid))

    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    done = False
    DIM, RST, YLW = "\033[2m", "\033[0m", "\033[33m"

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
                display = tc["args"].get("file_path") or tc["args"].get("command", "")
                if not isinstance(display, str):
                    display = json.dumps(tc["args"])[:100]
                sys.stderr.write(f"  {YLW}⚡ {tc['name']}{RST} {DIM}{str(display)[:100]}{RST}\n")
                sys.stderr.flush()

        elif etype == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                t = delta.get("text", "")
                if t:
                    text_parts.append(t)
            elif delta.get("type") == "thinking_delta":
                t = delta.get("thinking", "")
                if t:
                    sys.stderr.write(f"{DIM}💭{t}{RST}")
                    sys.stderr.flush()

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

    return {"messages": messages, "done": done, "pending_tool_ids": new_pending}


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


def _should_continue(state: AgentState) -> str:
    return "done" if state.get("done", False) else "next"


def build_claude_agent() -> Pregel:
    graph = StateGraph(AgentState)
    graph.add_node("start", _start_claude)
    graph.add_node("turn", _read_turn)
    graph.add_edge(START, "start")
    graph.add_edge("start", "turn")
    graph.add_conditional_edges("turn", _should_continue, {"next": "turn", "done": END})
    return graph.compile()
