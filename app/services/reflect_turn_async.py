"""Audit-only reflection after delivered (non-critical path)."""

from __future__ import annotations

import logging

from app.runtime.state import AgentState, append_audit, merge_state
from app.services.state_store import get_state_store

logger = logging.getLogger(__name__)


def reflect_turn_audit_sync(state: AgentState) -> AgentState:
    """
    Run reflection critique for observability only.
    Strips retry_planning / retry_reasoning so delivery path is unaffected.
    """
    from app.nodes.reflection_node import reflection_node

    task_id = str(state.get("task_id") or "")
    try:
        prior_status = state.get("status")
        updated = reflection_node(state)
        if prior_status:
            updated = merge_state(updated, status=prior_status)
        reflection = dict(updated.get("reflection_result") or {})
        reflection["retry_planning"] = False
        reflection["retry_reasoning"] = False
        reflection["route"] = "audit"
        reflection["async_audit"] = True
        verdict = reflection.get("verdict")
        if isinstance(verdict, dict):
            verdict = dict(verdict)
            verdict["recommended_action"] = "submit"
            reflection["verdict"] = verdict
        updated = merge_state(
            updated,
            reflection_result=reflection,
            audit_log=append_audit(
                updated,
                "reflection_audit",
                "recorded",
                {"async": True, "issues": reflection.get("issues")},
            ),
        )
        get_state_store().save(updated)
        return updated
    except Exception as exc:
        logger.exception("reflect_turn_audit failed task_id=%s", task_id)
        return state
