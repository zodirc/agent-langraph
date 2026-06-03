"""SSE

API display helpers — avoid duplicating gate content in final_answer."""

from __future__ import annotations

from typing import Any, Optional

from app.runtime.state import AgentState

_GATE_REASONING_SOURCES = frozenset(
    {
        "steer_intent_confirmation",
        "steer_outcome_confirmation",
    }
)


def is_gate_reasoning_result(reasoning: Optional[dict[str, Any]]) -> bool:
    if not isinstance(reasoning, dict):
        return False
    structured = reasoning.get("structured")
    if not isinstance(structured, dict):
        return False
    return str(structured.get("source") or "") in _GATE_REASONING_SOURCES


def steer_confirmation_pending_any(payload: dict[str, Any]) -> bool:
    from app.services.steer_confirmation_actions import steer_confirmation_pending_any as _any

    return _any(payload)


def client_final_answer(state: AgentState) -> Optional[str]:
    """User-visible final_answer; None when a steer gate owns the turn surface."""
    payload = state.get("input_payload") or {}
    if steer_confirmation_pending_any(payload):
        return None
    if is_gate_reasoning_result(state.get("reasoning_result")):
        return None
    answer = str(state.get("final_answer") or "").strip()
    return answer or None


def build_gate_sse_fields(task_id: str, state: AgentState) -> dict[str, Any]:
    """Confirmation fields for SSE done / resume responses."""
    from app.services.mission_steer_confirm import steer_confirmation_pending
    from app.services.mission_steer_outcome_confirm import steer_outcome_confirmation_pending
    from app.services.steer_confirmation_actions import confirmation_sse_fields

    payload = state.get("input_payload") or {}
    pending_intent = steer_confirmation_pending(payload)
    pending_outcome = steer_outcome_confirmation_pending(payload)
    return {
        "steer_intent_pending_confirm": pending_intent,
        "steer_intent_confirmation": (
            payload.get("steer_intent_confirmation") if pending_intent else None
        ),
        "steer_outcome_pending_confirm": pending_outcome,
        "steer_outcome_confirmation": (
            payload.get("steer_outcome_confirmation") if pending_outcome else None
        ),
        **confirmation_sse_fields(task_id, payload),
    }
