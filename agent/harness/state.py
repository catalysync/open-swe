"""Holds harness state — the typed contract flowing between agent graph nodes."""

from __future__ import annotations

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import Annotated, NotRequired, TypedDict


class HarnessState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]

    # developer turn-loop internals (claude -p subprocess handle key + pending tool ids)
    proc_key: NotRequired[str]
    dev_done: NotRequired[bool]
    pending_tool_ids: NotRequired[list[str]]

    # pipeline state (ADR 0005 Pydantic-style contracts, stored as plain dicts
    # so LangGraph checkpoints serialize cleanly)
    task: NotRequired[str]
    project_root: NotRequired[str]
    base_ref: NotRequired[str]  # git SHA at run start — diff against this, not HEAD
    plan: NotRequired[str]
    review_quality: NotRequired[dict]
    review_security: NotRequired[dict]
    review: NotRequired[dict]
    validation: NotRequired[dict]
    retry_count: NotRequired[int]
    status: NotRequired[str]
    proposal: NotRequired[dict | None]
