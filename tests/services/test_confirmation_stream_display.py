"""Steer gate stream display — no duplicate final_answer surface."""

from app.runtime.state import TaskStatus, merge_state
from app.services.confirmation.stream_display import (
    client_final_answer,
    is_gate_reasoning_result,
    rejection_detail,
    streamed_answer_revoked,
)
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


def test_client_final_answer_none_when_rejected():
    state = merge_state(
        {"task_id": "t1", "session_id": "s1", "final_answer": "draft"},
        status=TaskStatus.REJECTED.value,
        reasoning_result={"summary": "你好"},
    )
    assert client_final_answer(state) is None


def test_streamed_answer_revoked_when_rejected_with_summary():
    state = merge_state(
        {"task_id": "t1", "session_id": "s1"},
        status=TaskStatus.REJECTED.value,
        reasoning_result={"summary": "你好"},
        output_guard_result={"issues": ["faithfulness:no injected evidence"]},
    )
    assert streamed_answer_revoked(state) is True
    assert "faithfulness" in rejection_detail(state)
