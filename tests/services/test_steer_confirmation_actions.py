"""Structured steer confirmation (no NL phrase lists)."""

from app.runtime.state import merge_state
from app.services.mission_steer import apply_steer_message
from app.services.mission_steer_confirm import (
    apply_steer_confirmation_pending,
    steer_confirmation_pending,
)
from app.services.steer_confirmation_actions import (
    build_confirmation_actions,
    try_apply_structured_confirm,
)


def test_build_confirmation_actions():
    actions = build_confirmation_actions("t1")
    assert actions["message"]["body"] == {"message": "", "confirm": True}
    assert actions["message"]["path"] == "/tasks/t1/message/stream"
    assert actions["resume"]["path"] == "/tasks/t1/message/stream"


def test_structured_confirm_via_steer():
    state = merge_state(
        {"task_id": "t1", "session_id": "s1"},
        input_payload=apply_steer_confirmation_pending(
            {"steer_applied_at": "t"},
            {"summary_text": "plan"},
            task_id="t1",
        ),
    )
    assert steer_confirmation_pending(state["input_payload"])
    assert state["input_payload"]["steer_intent_confirmation"].get("user_actions")
    confirmed = apply_steer_message(state, confirm=True)
    assert not steer_confirmation_pending(confirmed["input_payload"])


def test_try_apply_structured_confirm_noop_without_pending():
    state = merge_state(
        {"task_id": "t1", "session_id": "s1"},
        input_payload={},
    )
    assert try_apply_structured_confirm(state, confirm=True) is None
