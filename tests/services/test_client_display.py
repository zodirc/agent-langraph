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
    assert body["autonomous_ui"]["behavior"] == "manual_resume"


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


def test_wall_clock_pause_blocks_auto_resume():
    state = merge_state(
        {
            "task_id": "t-wall",
            "session_id": "s1",
            "status": TaskStatus.MISSION_PAUSED.value,
            "mission": {"kind": "writing", "execution_mode": "autonomous"},
            "mission_control": {
                "done": True,
                "reason": "wall_clock 7403s >= max_wall_sec 7200",
                "action": "pause",
            },
            "input_payload": {
                "goal": "novel",
                "writing_intent": {"enabled": True, "action": "append_body"},
            },
        },
    )
    body = build_mission_paused_payload(state, autonomous=True, steer_pause=False)
    assert body["autonomous_ui"]["behavior"] == "wall_clock_pause"
    assert any("墙钟" in line for line in body["system_lines"])
    assert not any(line.startswith("下一步：继续生成下一章") for line in body["system_lines"])


def test_autonomous_step_pause_is_manual_resume():
    state = merge_state(
        {
            "task_id": "t-manual",
            "session_id": "s1",
            "status": TaskStatus.MISSION_PAUSED.value,
            "mission": {"kind": "writing", "execution_mode": "autonomous"},
            "mission_control": {
                "done": True,
                "reason": "stepwise orchestration: awaiting user steer or resume",
                "action": "pause",
                "pause_reason": "step_checkpoint",
            },
            "input_payload": {"goal": "novel"},
        },
    )
    body = build_mission_paused_payload(state, autonomous=True, steer_pause=False)
    assert body["autonomous_ui"]["behavior"] == "manual_resume"
    assert "继续写作" in " ".join(body["autonomous_ui"]["system_lines"])


def test_orchestration_summary_truncates_completed_list():
    from app.services.mission_orchestrator import format_completed_labels

    labels = [f"append chapter {i}" for i in range(20)]
    text = format_completed_labels(labels, max_items=8)
    assert "另有 12 项" in text
    assert "append chapter 0" in text


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


def test_steer_queued_display_prefers_goal_over_stale_contract():
    steer_text = "你应该基于原电影来编写，人物需要为原电影人物，只改动剧情走向"
    state = merge_state(
        {
            "task_id": "t1",
            "session_id": "s1",
            "status": TaskStatus.MISSION_RUNNING.value,
            "interrupt_context": {"control_state": "INTERRUPT_REQUESTED"},
            "input_payload": {
                "turn_contract": {"primary_op": "reasoning"},
            },
            "pending_user_message": {"messages": [{"message": steer_text}]},
        },
    )
    d = build_steer_task_client_display(state, queued=True)
    text = "\n".join(d["system_lines"])
    assert "queued_goal:" in text
    assert "原电影" in text
    assert "supersedes_plan:" in text
    assert text.index("queued_goal:") < text.index("supersedes_plan:")
