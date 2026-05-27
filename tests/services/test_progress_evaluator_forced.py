from app.runtime.state import TaskStatus, merge_state
from app.services.progress_evaluator import evaluate_mission_control


def test_forced_reset_body_continues_despite_queued_steer(base_state):
    state = merge_state(
        base_state,
        status=TaskStatus.MISSION_RUNNING.value,
        mission={"kind": "writing", "budget": {"max_failures": 3}},
        progress={"consecutive_failures": 3},
        pending_user_message={
            "messages": [{"message": "queued steer", "queued_at": "t"}],
            "message": "queued steer",
        },
        input_payload={
            "mission_intervention": {
                "action": "reset_body",
                "force": True,
                "reason": "rewrite from scratch",
            },
        },
    )
    result = evaluate_mission_control(state)
    assert result.done is False
    assert result.action == "continue"
    assert "reset_body" in result.reason
