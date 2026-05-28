"""Harness graph — supervisor-pipeline topology (ADR 0001/0005).

START → planner → developer → (developer_turn ⇄ self while streaming)
      → reviewer → validator → aggregator → {developer on retry | END}

Every node runs on claude -p (ADR 0002). Developer keeps the live turn-loop
so Studio renders tool calls as they happen; reasoning nodes are text-only.
"""

from __future__ import annotations

from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.pregel import Pregel

from . import curator, nodes
from .state import HarnessState


def route_supervisor(state: HarnessState) -> Literal["planner", "end"]:
    return "end" if state.get("status") == "done" else "planner"


def route_developer(state: HarnessState) -> Literal["turn", "reviewer"]:
    return "reviewer" if state.get("dev_done") else "turn"


def route_aggregator(state: HarnessState) -> Literal["developer", "curator", "end"]:
    status = state.get("status")
    if status == "developing":
        return "developer"
    if status == "done":
        return "curator"
    return "end"  # escalated


def build_harness_graph(hitl: bool = True) -> Pregel:
    g = StateGraph(HarnessState)

    g.add_node("supervisor", nodes.supervisor_node)
    g.add_node("planner", nodes.planner_node)
    g.add_node("developer", nodes.developer_node)
    g.add_node("developer_turn", nodes.developer_turn)
    g.add_node("reviewer", nodes.reviewer_node)
    g.add_node("validator", nodes.validator_node)
    g.add_node("aggregator", nodes.aggregator_node)
    g.add_node("curator", curator.curator_propose)
    g.add_node("curator_apply", curator.curator_apply)

    g.add_edge(START, "supervisor")
    g.add_conditional_edges(
        "supervisor", route_supervisor,
        {"planner": "planner", "end": END},
    )
    g.add_edge("planner", "developer")
    g.add_edge("developer", "developer_turn")
    g.add_conditional_edges(
        "developer_turn", route_developer,
        {"turn": "developer_turn", "reviewer": "reviewer"},
    )
    g.add_edge("reviewer", "validator")
    g.add_edge("validator", "aggregator")
    g.add_conditional_edges(
        "aggregator", route_aggregator,
        {"developer": "developer", "curator": "curator", "end": END},
    )
    g.add_conditional_edges(
        "curator", curator.route_curator,
        {"apply": "curator_apply", "end": END},
    )
    g.add_edge("curator_apply", END)

    # HITL (ADR 0008): pause before writing a learned template so the human
    # approves/edits in Studio. Disabled for unattended runs (e.g. benchmarks).
    return g.compile(interrupt_before=["curator_apply"] if hitl else [])
