"""Steer gate stream display — no duplicate final_answer surface."""

from app.runtime.state import merge_state
from app.services.confirmation.stream_display import client_final_answer, is_gate_reasoning_result
from app.services.mission_steer_confirm import attach_steer_confirmation_to_state


def test_attach_steer_confirmation_clears_final_answer():
    state = merge_state(
        {"task_id": "t1", "session_id": "s1"},
        input_payload={
            "steer_intent_confirmation": {
                "summary_text": "将重写大纲",
                "phase": "intent",
            }
        },
    )
    updated = attach_steer_confirmation_to_state(state)
    assert updated.get("final_answer") is None
    assert is_gate_reasoning_result(updated.get("reasoning_result"))


def test_client_final_answer_none_when_gate_pending():
    state = merge_state(
        {"task_id": "t1", "session_id": "s1", "final_answer": "should hide"},
        input_payload={"steer_intent_pending_confirm": True},
    )
    assert client_final_answer(state) is None
