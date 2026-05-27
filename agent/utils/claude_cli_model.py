"""BaseChatModel that routes through the claude CLI binary."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class ClaudeCLIModel(BaseChatModel):
    model_name: str = "sonnet"

    @property
    def _llm_type(self) -> str:
        return "claude-cli"

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        prompt = self._messages_to_prompt(messages)
        cmd = ["claude", "-p", prompt, "--model", self.model_name, "--output-format", "stream-json", "--verbose", "--dangerously-skip-permissions"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        full_text = ""
        tool_calls: list[dict] = []
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
                            sys.stderr.write(f"{DIM}💭 {t}{RST}\n"); sys.stderr.flush()
                    elif btype == "text":
                        text = block.get("text", "")
                        full_text += text
                        sys.stderr.write(text); sys.stderr.flush()
                    elif btype == "tool_use":
                        name, inp, tid = block.get("name", "?"), block.get("input", {}), block.get("id", "")
                        tool_calls.append({"name": name, "args": inp, "id": tid})
                        sys.stderr.write(f"  {YLW}⚡ {name}{RST} {DIM}{self._fmt(name, inp)}{RST}\n"); sys.stderr.flush()
            elif etype == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "thinking_delta":
                    t = delta.get("thinking", "")
                    if t:
                        sys.stderr.write(f"{DIM}{t}{RST}"); sys.stderr.flush()
                elif delta.get("type") == "text_delta":
                    t = delta.get("text", "")
                    if t:
                        full_text += t; sys.stderr.write(t); sys.stderr.flush()
            elif etype == "content_block_stop":
                sys.stderr.write("\n"); sys.stderr.flush()
            elif etype == "result":
                rt = event.get("result", "")
                if rt and not full_text:
                    full_text = rt

        proc.wait()
        sys.stderr.write("\n"); sys.stderr.flush()
        if proc.returncode != 0:
            raise RuntimeError(f"claude cli error: {proc.stderr.read() if proc.stderr else ''}")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=full_text, tool_calls=tool_calls or []))])

    @staticmethod
    def _messages_to_prompt(messages: list[BaseMessage]) -> str:
        parts = []
        for msg in messages:
            c = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
            if isinstance(msg, SystemMessage): parts.append(f"SYSTEM: {c}")
            elif isinstance(msg, HumanMessage): parts.append(f"USER: {c}")
            elif isinstance(msg, AIMessage): parts.append(f"ASSISTANT: {c}")
            elif isinstance(msg, ToolMessage): parts.append(f"TOOL RESULT ({msg.tool_call_id}): {c}")
            else: parts.append(c)
        return "\n\n".join(parts)

    @staticmethod
    def _fmt(name: str, inp: dict) -> str:
        if name in ("Read", "read_file", "Write", "write_file"): return inp.get("file_path", inp.get("path", str(inp)))
        if name == "Edit": return inp.get("file_path", str(inp))
        if name == "Bash": return inp.get("command", str(inp))
        if name in ("Grep", "grep"): return f'"{inp.get("pattern", "")}" in {inp.get("path", ".")}'
        if name in ("Glob", "glob"): return inp.get("pattern", str(inp))
        if name == "Agent": return inp.get("description", str(inp))
        return json.dumps(inp)
