"""Unified verification and submission gate (WP-3.1)."""

from __future__ import annotations

from typing import Any

from app.nodes.output_guard_node import output_guard_node
from app.nodes.reflection_node import reflection_node
from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.state_store import get_state_store


def _submission_decision(state: AgentState) -> dict[str, Any]:
    status = str(state.get("status") or "")
    reflection = state.get("reflection_result") or {}
    guard = state.get("output_guard_result") or {}
    policy = str(state.get("policy_result") or "")

    if status == TaskStatus.REJECTED.value or policy == "REJECT":
        return {"decision": "reject", "reason": "verification_failed"}
    if state.get("review_required") or policy == "REVIEW":
        return {"decision": "human_review", "reason": "review_required"}
    route = str(reflection.get("route") or "")
    if route == "retry_reasoning":
        return {"decision": "retry_reasoning", "reason": reflection.get("summary") or "retry"}
    if route == "retry_planning":
        return {"decision": "retry_planning", "reason": reflection.get("summary") or "replan"}
    if guard and not guard.get("passed", True):
        return {"decision": "degrade", "reason": "guard_failed"}
    return {"decision": "submit", "reason": "ok"}


def verification_node(state: AgentState) -> AgentState:
    """
    Merge reflection critique + output guard into formal submission gate.

    Writes: verification_result, submission_decision, reflection_result, output_guard_result
    """
    after_reflection = reflection_node(state)
    after_guard = output_guard_node(after_reflection)

    verification_result = {
        "reflection": after_guard.get("reflection_result"),
        "guard": after_guard.get("output_guard_result"),
        "policy_result": after_guard.get("policy_result"),
        "status": after_guard.get("status"),
    }
    decision = _submission_decision(after_guard)

    updated = merge_state(
        after_guard,
        verification_result=verification_result,
        submission_decision=decision,
        current_node="verification",
        audit_log=append_audit(
            after_guard,
            "verification",
            str(decision.get("decision") or "submit"),
            decision,
        ),
    )
    get_state_store().save(updated)
    return updated
