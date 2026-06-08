"""Context governance main-graph node (optimization WP-2.1)."""

from __future__ import annotations

from app.runtime.state import AgentState, append_audit, merge_state
from app.services.context_budget import apply_context_budget_to_state
from app.services.prompt_context_gateway import context_governance_enabled, prepare_governed_payload
from app.services.state_store import get_state_store
from app.services.stream_progress import report_progress


def context_governance_node(state: AgentState) -> AgentState:
    """
    Apply budget buckets and governed context assembly before generation.

    Reads: plan, tool_results, retrieved_knowledge, token_budget
    Writes: context_budget_buckets, input_payload.context_governance
    """
    updated = apply_context_budget_to_state(state)
    payload = dict(updated.get("input_payload") or {})
    purpose = "reasoning"
    if payload.get("writing_intent"):
        purpose = "writing"

    if context_governance_enabled():
        payload, _envelope = prepare_governed_payload(updated, purpose=purpose, payload=payload)

    buckets = updated.get("context_budget_buckets") or {}
    report_progress("上下文预算治理完成，准备生成…")

    updated = merge_state(
        updated,
        input_payload=payload,
        current_node="context_governance",
        audit_log=append_audit(
            updated,
            "context_governance",
            "governed",
            {
                "total_budget": buckets.get("total_budget"),
                "soft_limit": buckets.get("soft_limit"),
                "hard_limit": buckets.get("hard_limit"),
            },
        ),
    )
    get_state_store().save(updated)
    return updated
