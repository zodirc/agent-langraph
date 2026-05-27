"""Server-composed client display (no action-specific Web branching)."""

from app.runtime.state import merge_state, TaskStatus
from app.services.client_display import (
    build_mission_paused_payload,
    build_steer_task_client_display,
)


def test_mission_paused_uses_intervention_reason():
    state = merge_state(
        {
            "task_id": "t1",
            "session_id": "s1",
            "status": TaskStatus.MISSION_PAUSED.value,
            "mission": {"kind": "writing", "execution_mode": "autonomous"},
            "mission_control": {"done": True, "reason": "user steer message queued", "action": "pause"},
            "input_payload": {
                "mission_intervention": {
                    "action": "rewrite_outline",
                    "force": True,
                    "reason": "Will rewrite outline per user timeline feedback.",
                },
            },
        },
    )
    body = build_mission_paused_payload(state, autonomous=True, steer_pause=True)
    assert any("timeline" in line for line in body["system_lines"])
    assert body["autonomous_ui"]["behavior"] == "resume_after_steer"


def test_outcome_gate_blocks_auto_resume_when_snapshot_false(isolated_stores):
    """DB still has outcome pending while in-memory payload cleared the flag."""
    from app.services.state_store import get_state_store

    task_id = "t-outcome-gate"
    store = get_state_store()
    store.save(
        merge_state(
            {
                "task_id": task_id,
                "session_id": "s1",
                "status": TaskStatus.MISSION_PAUSED.value,
                "mission": {"kind": "writing", "execution_mode": "autonomous"},
                "input_payload": {
                    "steer_outcome_pending_confirm": True,
                    "steer_outcome_confirmation": {"summary_text": "outline done"},
                },
            },
        )
    )
    state = merge_state(
        {
            "task_id": task_id,
            "session_id": "s1",
            "status": TaskStatus.MISSION_PAUSED.value,
            "mission": {"kind": "writing", "execution_mode": "autonomous"},
            "input_payload": {"steer_outcome_pending_confirm": False},
        },
    )
    body = build_mission_paused_payload(state, autonomous=True, steer_pause=False)
    assert body["autonomous_ui"]["behavior"] == "wait_outcome_confirm"
    assert body["autonomous_ui"]["resume_confirm"] is True


def test_failure_pause_blocks_auto_resume(isolated_stores):
    state = merge_state(
        {
            "task_id": "t-fail",
            "session_id": "s1",
            "status": TaskStatus.MISSION_PAUSED.value,
            "mission": {"kind": "writing", "execution_mode": "autonomous"},
            "mission_control": {
                "done": True,
                "reason": "consecutive_failures 3 >= 3",
                "action": "pause",
            },
            "input_payload": {"goal": "novel"},
        },
    )
    body = build_mission_paused_payload(state, autonomous=True, steer_pause=False)
    assert body["autonomous_ui"]["behavior"] == "failure_pause"


def test_failure_pause_from_progress_counter(isolated_stores):
    state = merge_state(
        {
            "task_id": "t-fail2",
            "session_id": "s1",
            "status": TaskStatus.MISSION_PAUSED.value,
            "mission": {"kind": "writing", "execution_mode": "autonomous", "budget": {"max_failures": 3}},
            "progress": {"consecutive_failures": 3},
            "input_payload": {"goal": "novel"},
        },
    )
    body = build_mission_paused_payload(state, autonomous=True, steer_pause=False)
    assert body["autonomous_ui"]["behavior"] == "failure_pause"


def test_steer_queued_display():
    state = merge_state(
        {
            "task_id": "t1",
            "session_id": "s1",
            "status": TaskStatus.MISSION_RUNNING.value,
            "pending_user_message": {"messages": [{"message": "fix plot"}]},
        },
    )
    d = build_steer_task_client_display(state, queued=True)
    assert d["kind"] == "steer_queued"
    assert any("queue" in line.lower() for line in d["system_lines"])
