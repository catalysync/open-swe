"""Harness graph — supervisor-pipeline topology (ADR 0001/0005).

START → planner → developer → (developer_turn ⇄ self while streaming)
      → reviewer → validator → aggregator → {developer on retry | END}

Every node runs on claude -p (ADR 0002). Developer keeps the live turn-loop
so Studio renders tool calls as they happen; reasoning nodes are text-only.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.pregel import Pregel

from . import nodes
from .state import HarnessState


def build_harness_graph() -> Pregel:
    g = StateGraph(HarnessState)

    g.add_node("supervisor", nodes.supervisor_node)
    g.add_node("planner", nodes.planner_node)
    g.add_node("developer", nodes.developer_node)
    g.add_node("developer_turn", nodes.developer_turn)
    g.add_node("reviewer", nodes.reviewer_node)
    g.add_node("validator", nodes.validator_node)
    g.add_node("aggregator", nodes.aggregator_node)

    g.add_edge(START, "supervisor")
    g.add_edge("supervisor", "planner")
    g.add_edge("planner", "developer")
    g.add_edge("developer", "developer_turn")
    g.add_conditional_edges(
        "developer_turn", nodes.route_developer,
        {"turn": "developer_turn", "reviewer": "reviewer"},
    )
    g.add_edge("reviewer", "validator")
    g.add_edge("validator", "aggregator")
    g.add_conditional_edges(
        "aggregator", nodes.route_aggregator,
        {"developer": "developer", "end": END},
    )

    return g.compile()
