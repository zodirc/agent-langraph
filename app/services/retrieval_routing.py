"""Attach retrieval decision at routing/planning boundary (§4.1.1 / §6)."""

from __future__ import annotations

from typing import Any

from app.runtime.state import AgentState
from app.services.query_builder import build_query_object
from app.services.retrieval_decision import build_retrieval_decision
from app.services.task_drift import detect_task_drift


def attach_retrieval_context(state: AgentState | dict[str, Any]) -> dict[str, Any]:
    """
    Build and persist retrieval_decision + query_object early in the pipeline.

    Called from planning_node, route_after_planning, writing_node preamble.
    """
    decision = build_retrieval_decision(state)
    query = build_query_object(state, decision)
    drift = detect_task_drift(state)
    if drift.get("drifted"):
        query.task_constraints.append("task_drift_detected")
        query.debug_info["task_drift"] = drift

    return {
        "retrieval_decision": decision.model_dump(),
        "query_object": query.model_dump(),
        "task_drift": drift,
    }


def purpose_for_llm_node(state: AgentState | dict[str, Any], node: str) -> str:
    """Purpose-aware policy per node (§6.2)."""
    decision = state.get("retrieval_decision")
    if isinstance(decision, dict) and decision.get("purpose"):
        purpose = str(decision["purpose"])
    else:
        purpose = build_retrieval_decision(state).purpose

    if node == "writing":
        return "planning_background" if purpose == "comparative_summary" else "procedural_howto"
    if node == "planning":
        return "planning_background"
    return purpose


def sync_skip_retrieval_with_decision(state: AgentState | dict[str, Any]) -> bool:
    """Align legacy skip_retrieval flag with structured decision."""
    decision = state.get("retrieval_decision")
    if isinstance(decision, dict):
        return not bool(decision.get("need_retrieval", True))
    return bool(state.get("skip_retrieval"))
