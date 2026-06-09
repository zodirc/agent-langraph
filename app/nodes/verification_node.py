"""Unified verification and submission gate (WP-3.1)."""

from __future__ import annotations

from typing import Any

from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.state_store import get_state_store


def _submission_decision(state: AgentState) -> dict[str, Any]:
    status = str(state.get("status") or "")
    guard = state.get("output_guard_result") or {}
    policy = str(state.get("policy_result") or "")

    if status == TaskStatus.REJECTED.value or policy == "REJECT":
        return {"decision": "reject", "reason": "verification_failed"}
    if state.get("review_required") or policy == "REVIEW":
        return {"decision": "human_review", "reason": "review_required"}
    if guard and not guard.get("passed", True):
        return {"decision": "degrade", "reason": "guard_failed"}
    return {"decision": "submit", "reason": "ok"}


def verification_node(state: AgentState) -> AgentState:
    """
    Formal submission gate after pre-stream guard + async reflection audit.

    Writes: verification_result, submission_decision
    """
    guard = state.get("output_guard_result") or {}
    verification_result = {
        "guard": guard,
        "policy_result": state.get("policy_result"),
        "status": state.get("status"),
    }
    decision = _submission_decision(state)

    updated = merge_state(
        state,
        verification_result=verification_result,
        submission_decision=decision,
        current_node="verification",
        audit_log=append_audit(
            state,
            "verification",
            str(decision.get("decision") or "submit"),
            decision,
        ),
    )
    get_state_store().save(updated)
    return updated
