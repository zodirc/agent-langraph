"""Server-composed client display (unified core: generic pause/steer only)."""

from app.runtime.state import TaskStatus, merge_state
from app.services.client_display import (
    build_mission_paused_payload,
    build_steer_task_client_display,
    confirmation_panel_display,
)


def test_paused_payload_user_requested_pause():
    state = merge_state(
        {
            "task_id": "t1",
            "session_id": "s1",
            "status": TaskStatus.PAUSED.value,
            "interrupt_context": {"pause_reason": "user_requested_pause"},
        },
    )
    body = build_mission_paused_payload(state)
    assert body["task_id"] == "t1"
    assert body["display"]["pause_kind"] == "user_control"
    assert any("暂停" in line for line in body["system_lines"])


def test_paused_payload_cancel_reason():
    state = merge_state(
        {
            "task_id": "t2",
            "session_id": "s1",
            "status": TaskStatus.CANCELLED.value,
            "interrupt_context": {"pause_reason": "user_requested_cancel"},
        },
    )
    body = build_mission_paused_payload(state)
    assert body["display"]["pause_kind"] == "user_control"
    assert any("取消" in line for line in body["system_lines"])


def test_paused_payload_steer_pause_kind():
    state = merge_state(
        {
            "task_id": "t3",
            "session_id": "s1",
            "status": TaskStatus.PAUSED.value,
            "interrupt_context": {"reason": "steer message queued"},
        },
    )
    body = build_mission_paused_payload(state, steer_pause=True)
    assert body["display"]["pause_kind"] == "steer"
    assert "steer message queued" in body["system_lines"]


def test_paused_payload_defaults_when_no_context():
    state = merge_state(
        {"task_id": "t4", "session_id": "s1", "status": TaskStatus.PAUSED.value},
    )
    body = build_mission_paused_payload(state)
    assert body["system_lines"] == ["task_paused"]
    assert body["display"]["pause_kind"] == "stepwise"


def test_steer_queued_display():
    state = merge_state(
        {
            "task_id": "t1",
            "session_id": "s1",
            "status": TaskStatus.RUNNING.value,
            "pending_user_message": {"messages": [{"message": "fix plot"}]},
        },
    )
    d = build_steer_task_client_display(state, queued=True)
    assert d["kind"] == "steer_queued"
    assert any("queue" in line.lower() for line in d["system_lines"])
    assert "queued_goal: fix plot" in d["system_lines"]
    assert d["display"]["queue_depth"] == 1


def test_steer_applied_display():
    state = merge_state(
        {
            "task_id": "t1",
            "session_id": "s1",
            "status": TaskStatus.PAUSED.value,
            "interrupt_context": {"reason": "steer applied"},
        },
    )
    d = build_steer_task_client_display(state, queued=False)
    assert d["kind"] == "steer_applied"
    assert d["display"]["queued"] is False


def test_confirmation_panel_display_phases():
    assert confirmation_panel_display("intent")["title"] == "steer_intent_confirmation"
    assert confirmation_panel_display("outcome")["title"] == "steer_outcome_confirmation"
    assert confirmation_panel_display("other")["title"] == "steer_confirmation"
