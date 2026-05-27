"""Claude CLI agent — replaces Deep Agents with claude -p."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.pregel import Pregel
from typing_extensions import Annotated, TypedDict


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


def _run_claude(state: AgentState) -> dict:
    """Single node: run claude -p with the last user message."""
    messages = state["messages"]
    last_msg = None
    system_parts = []

    for msg in messages:
        if isinstance(msg, HumanMessage):
            last_msg = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
        elif hasattr(msg, "type") and msg.type == "human":
            last_msg = msg.content if isinstance(msg.content, str) else str(msg.content)
        elif hasattr(msg, "type") and msg.type == "system":
            system_parts.append(msg.content if isinstance(msg.content, str) else str(msg.content))

    if not last_msg:
        return {"messages": [AIMessage(content="No task provided.")]}

    prompt = last_msg
    if system_parts:
        prompt = "\n\n".join(system_parts) + "\n\n" + prompt

    cmd = [
        "claude", "-p", prompt,
        "--model", "sonnet",
        "--output-format", "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
    ]

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )

    full_text = ""
    DIM, RST, YLW = "\033[2m", "\033[0m", "\033[33m"

    while True:
        line = proc.stdout.readline()
        if not line and proc.poll() is not None:
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
            for block in event.get("message", {}).get("content", []):
                btype = block.get("type")
                if btype == "thinking":
                    t = block.get("thinking", "")
                    if t:
                        sys.stderr.write(f"{DIM}💭 {t}{RST}\n")
                        sys.stderr.flush()
                elif btype == "text":
                    text = block.get("text", "")
                    full_text += text
                    sys.stderr.write(text)
                    sys.stderr.flush()
                elif btype == "tool_use":
                    name = block.get("name", "?")
                    inp = block.get("input", {})
                    display = inp.get("file_path", inp.get("command", inp.get("pattern", json.dumps(inp)[:100])))
                    sys.stderr.write(f"  {YLW}⚡ {name}{RST} {DIM}{display}{RST}\n")
                    sys.stderr.flush()

        elif etype == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "thinking_delta":
                t = delta.get("thinking", "")
                if t:
                    sys.stderr.write(f"{DIM}{t}{RST}")
                    sys.stderr.flush()
            elif delta.get("type") == "text_delta":
                t = delta.get("text", "")
                if t:
                    full_text += t
                    sys.stderr.write(t)
                    sys.stderr.flush()

        elif etype == "content_block_stop":
            sys.stderr.write("\n")
            sys.stderr.flush()

        elif etype == "result":
            rt = event.get("result", "")
            if rt and not full_text:
                full_text = rt

    proc.wait()
    sys.stderr.write("\n")
    sys.stderr.flush()

    if proc.returncode != 0:
        stderr = proc.stderr.read() if proc.stderr else ""
        full_text = f"Error: {stderr}"

    return {"messages": [AIMessage(content=full_text)]}


def build_claude_agent() -> Pregel:
    graph = StateGraph(AgentState)
    graph.add_node("agent", _run_claude)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", END)
    return graph.compile()
