"""State for the `hunt` graph — find bugs + missing-coverage, fix each on its
own sequenced branch off a per-task integration branch, one PR per fix.
"""

from __future__ import annotations

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import Annotated, NotRequired, TypedDict


class HuntState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]

    # config / setup
    task: NotRequired[str]
    project_root: NotRequired[str]
    base_ref: NotRequired[str]      # HEAD the task branch is cut from
    task_branch: NotRequired[str]   # hunt/<slug> — integration branch; PRs target this

    # discovered work: each {title, file, kind (bug|coverage|feature), severity, fix}
    bugs: NotRequired[list[dict]]
    idx: NotRequired[int]

    # developer turn-loop internals
    proc_key: NotRequired[str]
    dev_done: NotRequired[bool]
    pending_tool_ids: NotRequired[list[str]]

    # per-bug gate state
    bug_retries: NotRequired[int]
    gate_ok: NotRequired[int]       # 1 pass / 0 fail for the current bug
