"""State for the `mine` graph — replicate a reference (video/images/docs) into
an existing project, section by section, with optional visual-parity gating.
"""

from __future__ import annotations

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import Annotated, NotRequired, TypedDict


class MineState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]

    # config (from the run): what to mine, where to build it, how strict
    source: NotRequired[str]          # path to a video / images dir / doc(s)
    source_kind: NotRequired[str]     # video | images | docs (auto-detected)
    project_root: NotRequired[str]
    visual: NotRequired[bool]         # gate on visual parity vs the reference

    # decomposed work units; each: {label, ref, ref_kind}  (ref_kind: image|text)
    sections: NotRequired[list[dict]]
    idx: NotRequired[int]             # current section index

    # developer turn-loop internals (reused from the build harness)
    proc_key: NotRequired[str]
    dev_done: NotRequired[bool]
    pending_tool_ids: NotRequired[list[str]]

    # per-section gate state
    section_retries: NotRequired[int]
    parity: NotRequired[int]          # last visual-parity score 0-100
