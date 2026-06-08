"""Foreground ACK node — instant user-visible feedback (optimization WP-1.2)."""

from __future__ import annotations

import time

from app.runtime.state import AgentState, append_audit, merge_state
from app.services.foreground_ack import build_foreground_ack
from app.services.state_store import get_state_store
from app.services.stream_progress import report_ack


def acknowledge_node(state: AgentState) -> AgentState:
    """
    Emit foreground ACK before deep execution (retrieval / tools / LLM).

    Reads: event_type, event_id, input_payload
    Writes: foreground_status, input_payload.foreground_ack, audit_log
    """
    started = time.monotonic()
    ack = build_foreground_ack(state)
    ack["latency_ms"] = max(0, int((time.monotonic() - started) * 1000))

    payload = dict(state.get("input_payload") or {})
    payload["foreground_ack"] = ack

    foreground_status = {
        "phase": "acknowledged",
        "last_ack": ack,
        "updated_at": ack["emitted_at"],
    }

    report_ack(
        node="acknowledge",
        ack=ack,
        task_id=str(state.get("task_id") or ""),
    )

    updated = merge_state(
        state,
        input_payload=payload,
        foreground_status=foreground_status,
        current_node="acknowledge",
        audit_log=append_audit(
            state,
            "acknowledge",
            "ack",
            {
                "event_type": ack.get("event_type"),
                "latency_ms": ack.get("latency_ms"),
                "detected_replan": ack.get("detected_replan"),
            },
        ),
    )
    get_state_store().save(updated)
    return updated
