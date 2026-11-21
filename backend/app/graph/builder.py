"""Assemble the invoice `StateGraph`."""

import itertools
from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.graph.deps import GraphDeps
from app.graph.nodes import build_nodes, needs_review
from app.graph.state import CaseState

LINEAR = [
    "intake",
    "document_check",
    "extract",
    "vendor_match",
    "retrieve_policy",
    "rules",
    "register_case",
    "route_exception",
]


def _then(next_node: str) -> Callable[[CaseState], str]:
    def route(state: CaseState) -> str:
        return END if state.get("failed") else next_node

    return route


def _after_routing(state: CaseState) -> str:
    if state.get("failed"):
        return END
    if state.get("decision") == "REJECTED":
        return "explain"  # rejection is final: no review, approvals or posting
    return "human_review" if needs_review(state) else "approval_routing"


def _after_review(state: CaseState) -> str:
    if state.get("failed"):
        return END
    action = (state.get("reviews") or [{}])[-1].get("action")
    if action == "REQUEST_INFO":
        return "human_review"  # keep waiting for the next human answer
    if action == "REJECT":
        return "explain"
    return "approval_routing"


def _after_approvals(state: CaseState) -> str:
    if state.get("failed"):
        return END
    if state.get("final_decision") == "REJECTED":
        return "explain"
    if state.get("approvals_pending"):
        return "approval_routing"  # pause for the next approver
    return "post_erp"


def _after_verify(state: CaseState) -> str:
    if state.get("failed"):
        return END
    return "explain" if state.get("erp_verified") else "verify_post"


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
    g.add_conditional_edges(
        "route_exception", _after_routing, ["human_review", "approval_routing", "explain", END]
    )
    g.add_conditional_edges(
        "human_review", _after_review, ["human_review", "approval_routing", "explain", END]
    )
    g.add_conditional_edges(
        "approval_routing", _after_approvals, ["approval_routing", "post_erp", "explain", END]
    )
    g.add_conditional_edges("post_erp", _then("verify_post"), ["verify_post", END])
    g.add_conditional_edges("verify_post", _after_verify, ["verify_post", "explain", END])
    g.add_conditional_edges("explain", _then("close_case"), ["close_case", END])
    g.add_edge("close_case", END)
    return g.compile(checkpointer=checkpointer)
