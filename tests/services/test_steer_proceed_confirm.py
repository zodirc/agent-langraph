"""Proceed messages approve pending steer intent gates."""

from app.runtime.state import merge_state, TaskStatus
from app.services.mission_steer import apply_steer_message
from app.services.mission_steer_confirm import apply_steer_confirmation_pending


def test_please_proceed_confirms_pending_intent(base_state):
    payload = apply_steer_confirmation_pending(
        {"goal": "复查"},
        {"summary_text": "batch review", "primary_op": "pause"},
        task_id=base_state["task_id"],
    )
    state = merge_state(
        base_state,
        input_payload=payload,
        status=TaskStatus.MISSION_PAUSED.value,
        mission={"kind": "writing"},
    )
    out = apply_steer_message(state, "请进行", source="session_turn", persist=False)
    pl = out.get("input_payload") or {}
    assert pl.get("steer_intent_confirmed") is True
    assert not pl.get("steer_intent_pending_confirm")
