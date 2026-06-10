"""Eval capture node — regression candidate emission (WP-3.2)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.state_store import get_state_store


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _eligible_for_capture(state: AgentState) -> bool:
    status = str(state.get("status") or "")
    if status not in {TaskStatus.COMPLETED.value, TaskStatus.PAUSED.value}:
        return False
    payload = state.get("input_payload") or {}
    if payload.get("skip_eval_capture"):
        return False
    if payload.get("user_feedback") == "positive":
        return True
    if status == TaskStatus.COMPLETED.value and state.get("final_answer"):
        return True
    return False


def eval_capture_node(state: AgentState) -> AgentState:
    payload = dict(state.get("input_payload") or {})
    capture: dict = {
        "captured_at": _now_iso(),
        "eligible": _eligible_for_capture(state),
        "task_id": state.get("task_id"),
        "event_type": state.get("event_type"),
        "status": state.get("status"),
        "submission_decision": state.get("submission_decision"),
    }
    bg = state.get("background_status") or {}
    if isinstance(bg.get("checkpoint_recovery"), dict):
        capture["checkpoint_recovery"] = bg["checkpoint_recovery"]
    if capture["eligible"]:
        capture["regression_candidate"] = {
            "goal": payload.get("goal"),
            "plan": state.get("plan"),
            "final_answer_preview": (state.get("final_answer") or "")[:500],
        }

    updated = merge_state(
        state,
        eval_capture=capture,
        current_node="eval_capture",
        audit_log=append_audit(state, "eval_capture", "recorded", {"eligible": capture["eligible"]}),
    )
    get_state_store().save(updated)
    return updated
