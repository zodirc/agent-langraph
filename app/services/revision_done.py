"""RevisionDone — edit succeeded and scope aligned → turn may finalize."""

from __future__ import annotations

from typing import Any

from app.domain.revision_intent import RevisionIntent
from app.runtime.state import AgentState
from app.services.intent_snapshot import current_intent_snapshot


def _revision_intent_from_state(state: AgentState) -> RevisionIntent | None:
    snap = current_intent_snapshot(state)
    if snap and snap.revision_intent:
        return RevisionIntent.from_dict(snap.revision_intent)
    payload = state.get("input_payload") or {}
    obs = state.get("intent_observation") or {}
    raw = obs.get("revision_intent") or payload.get("revision_intent")
    return RevisionIntent.from_dict(raw) if isinstance(raw, dict) else None


def is_revision_turn(state: AgentState) -> bool:
    snap = current_intent_snapshot(state)
    if snap and snap.is_revision:
        return True
    obs = state.get("intent_observation") or {}
    return bool(obs.get("is_revision"))


def is_revision_done(state: AgentState) -> tuple[bool, str]:
    """
    True when:
    1. Last edit_text_artifact succeeded with replacements >= 1
    2. Scope aligns with RevisionIntent
    3. All explicit edits applied (single batch counts)
    4. completion_policy != batch_until_done or queue empty
    """
    if not is_revision_turn(state):
        return False, "not_revision_turn"

    ri = _revision_intent_from_state(state)
    if ri is None:
        return False, "no_revision_intent"

    edit_results = [
        item
        for item in (state.get("tool_results") or [])
        if str(item.get("tool") or "") == "edit_text_artifact"
        and item.get("status") == "ok"
    ]
    if not edit_results:
        return False, "no_edit_result"

    last = edit_results[-1]
    result_body = last.get("result") or {}
    replacements = int(result_body.get("replacements") or 0)
    if replacements < 1:
        return False, "zero_replacements"

    selection = result_body.get("selection") or {}
    scope = selection.get("scope") or {}
    if ri.edits:
        first = ri.edits[0]
        if first.start_line is not None and scope.get("start_line") is not None:
            if int(scope.get("start_line") or 0) != int(first.start_line):
                return False, "scope_mismatch"

    if ri.completion_policy == "batch_until_done":
        pending = payload_pending_edits(state, ri)
        if pending:
            return False, "batch_pending"

    return True, "revision_done"


def mark_revision_completed(state: AgentState) -> AgentState:
    """Set audit fields and emit revision_done turn event."""
    from app.runtime.state import merge_state
    from app.services.turn_event_log import record_turn_event

    payload = dict(state.get("input_payload") or {})
    payload["revision_done"] = True
    updated = merge_state(state, input_payload=payload)
    return record_turn_event(
        updated,
        "revision_done",
        "revision",
        "revision_done",
        {"revision_done": True},
    )


def payload_pending_edits(state: AgentState, ri: RevisionIntent) -> list[dict[str, Any]]:
    payload = state.get("input_payload") or {}
    applied = payload.get("revision_edits_applied") or []
    applied_count = len(applied) if isinstance(applied, list) else 0
    if applied_count >= len(ri.edits):
        return []
    return [e.to_dict() for e in ri.edits[applied_count:]]
