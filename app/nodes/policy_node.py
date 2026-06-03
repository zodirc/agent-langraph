"""策略节点

Policy: score reasoning → CONTINUE | REVIEW | ESCALATE | REJECT.
Route: route_after_policy_to_guard → output_guard | human_review | rejected."""

from __future__ import annotations

from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.metrics_service import get_metrics_service
from app.services.policy_engine import get_policy_engine
from app.services.reasoning_trace import report_boundary
from app.services.state_store import get_state_store


def policy_node(state: AgentState) -> AgentState:
    """
    Apply policy rules to reasoning output.

    Reads: reasoning_result, tool_results, review_required, input_payload
    Writes: policy_result, review_required, status, current_node, audit_log
    """
    try:
        reasoning = state.get("reasoning_result") or {}
        payload = state.get("input_payload", {})
        user_role = str(payload.get("user_role", "user"))
        report_boundary("policy", "enter")
        engine = get_policy_engine()
        decision = engine.evaluate(
            reasoning_result=reasoning,
            tool_results=state.get("tool_results"),
            risk_level=reasoning.get("risk_level"),
            user_role=user_role,
            review_required=bool(state.get("review_required")),
        )

        metrics = get_metrics_service()
        if decision.result == "REVIEW":
            metrics.inc_policy_review()
        elif decision.result == "REJECT":
            metrics.inc_policy_reject()

        needs_review = decision.result in ("REVIEW", "ESCALATE")
        updated = merge_state(
            state,
            policy_result=decision.result,
            review_required=needs_review,
            status=TaskStatus.POLICY_CHECKED.value,
            current_node="policy",
            audit_log=append_audit(
                state,
                "policy",
                "success",
                {"result": decision.result, "reason": decision.reason},
            ),
        )
        get_state_store().save(updated)
        return updated
    except Exception as exc:
        return merge_state(
            state,
            errors=list(state.get("errors", [])) + [f"policy: {exc}"],
            retry_count=state.get("retry_count", 0) + 1,
            status=TaskStatus.FAILED.value,
            current_node="policy",
            audit_log=append_audit(state, "policy", "error", {"detail": str(exc)}),
        )
