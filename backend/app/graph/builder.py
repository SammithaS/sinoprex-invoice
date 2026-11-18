"""Assemble the invoice `StateGraph` (plan §6)."""

import itertools
from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.graph.deps import GraphDeps
from app.graph.nodes import NEEDS_HUMAN, build_nodes
from app.graph.state import CaseState

LINEAR = [
    "intake",
    "document_check",
    "extract",
    "vendor_match",
    "retrieve_policy",
    "rules",
    "register_case",
]


def _then(next_node: str) -> Callable[[CaseState], str]:
    def route(state: CaseState) -> str:
        return END if state.get("failed") else next_node

    return route


def _after_register(state: CaseState) -> str:
    if state.get("failed"):
        return END
    return "human_review" if state.get("decision") in NEEDS_HUMAN else "explain"


def _after_review(state: CaseState) -> str:
    if state.get("failed"):
        return END
    last = (state.get("reviews") or [{}])[-1]
    # REQUEST_INFO keeps the case waiting: pause again for the next human answer.
    return "human_review" if last.get("action") == "REQUEST_INFO" else "explain"


def build_graph(
    deps: GraphDeps, checkpointer: BaseCheckpointSaver[Any] | None
) -> CompiledStateGraph[CaseState, None, CaseState, CaseState]:
    nodes = build_nodes(deps)
    g: StateGraph[CaseState, None, CaseState, CaseState] = StateGraph(CaseState)
    for name, fn in nodes.items():
        g.add_node(name, fn)  # type: ignore[call-overload]  # async fn returning a partial update
    g.add_edge(START, LINEAR[0])
    for current, nxt in itertools.pairwise(LINEAR):
        g.add_conditional_edges(current, _then(nxt), [nxt, END])
    g.add_conditional_edges("register_case", _after_register, ["human_review", "explain", END])
    g.add_conditional_edges("human_review", _after_review, ["human_review", "explain", END])
    g.add_conditional_edges("explain", _then("close_case"), ["close_case", END])
    g.add_edge("close_case", END)
    return g.compile(checkpointer=checkpointer)
