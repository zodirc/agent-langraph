"""Event classification node — unified runtime entry (optimization WP-1.1)."""

from __future__ import annotations

from app.runtime.state import AgentState, append_audit, merge_state
from app.services.event_classification import classify_user_event
from app.services.state_store import get_state_store


def event_classification_node(state: AgentState) -> AgentState:
    """
    Classify inbound user control event and stamp state for downstream layers.

    Reads: input_payload, session_turn, interrupt_context, conversation_history
    Writes: event_type, event_id, input_payload.event_classification, audit_log
    """
    payload = dict(state.get("input_payload") or {})
    classification = classify_user_event(state, payload=payload)
    payload["event_classification"] = classification.to_dict()

    updated = merge_state(
        state,
        input_payload=payload,
        event_type=classification.event_type,
        event_id=classification.event_id,
        current_node="event_classification",
        audit_log=append_audit(
            state,
            "event_classification",
            "classified",
            classification.to_dict(),
        ),
    )
    get_state_store().save(updated)
    return updated
