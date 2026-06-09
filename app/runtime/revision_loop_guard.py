"""Read-loop + intent-loop circuit breakers for revision fast path."""

from __future__ import annotations

from app.config.settings import settings
from app.runtime.state import AgentState, merge_state
from app.services.intent_snapshot import current_intent_snapshot
from app.services.revision_done import is_revision_turn


def _revision_cfg() -> dict:
    cfg = getattr(settings, "REVISION_CONFIG", {}) or {}
    return cfg if isinstance(cfg, dict) else {}


def read_loop_max() -> int:
    return int(_revision_cfg().get("read_loop_max", 2))


def intent_recompute_max() -> int:
    return int(_revision_cfg().get("intent_recompute_max", 1))


def count_reads_this_turn(state: AgentState) -> int:
    reads = 0
    edits = 0
    for item in state.get("tool_results") or []:
        tool = str(item.get("tool") or "")
        if tool == "read_text_artifact" and item.get("status") == "ok":
            reads += 1
        if tool == "edit_text_artifact" and item.get("status") == "ok":
            edits += 1
    if edits > 0:
        return 0
    return reads


def revision_read_loop_triggered(state: AgentState) -> bool:
    if not is_revision_turn(state):
        return False
    return count_reads_this_turn(state) >= read_loop_max()


def revision_intent_loop_triggered(state: AgentState) -> bool:
    if not is_revision_turn(state):
        return False
    snap = current_intent_snapshot(state)
    if snap is None or snap.snapshot_status != "frozen":
        return False
    payload = state.get("input_payload") or {}
    recompute_count = int(payload.get("intent_recompute_count") or 0)
    return recompute_count >= intent_recompute_max()


def revision_loop_guard_triggered(state: AgentState) -> tuple[bool, str]:
    if revision_read_loop_triggered(state):
        return True, "read_loop"
    if revision_intent_loop_triggered(state):
        return True, "intent_loop"
    return False, ""


def apply_revision_loop_guard(state: AgentState) -> AgentState:
    triggered, reason = revision_loop_guard_triggered(state)
    if not triggered:
        return state
    payload = dict(state.get("input_payload") or {})
    payload["revision_loop_guard_triggered"] = True
    payload["revision_loop_guard_reason"] = reason
    from app.services.turn_event_log import record_turn_event

    updated = merge_state(state, input_payload=payload)
    return record_turn_event(
        updated,
        "revision_loop_guard_triggered",
        "revision_guard",
        "revision_loop_guard",
        {"reason": reason},
    )


def should_block_incremental_planning(state: AgentState) -> bool:
    """Block return to incremental_planning when revision loop guard fired."""
    payload = state.get("input_payload") or {}
    if payload.get("revision_loop_guard_triggered"):
        return True
    triggered, _ = revision_loop_guard_triggered(state)
    return triggered
