from app.runtime.state import TaskStatus, merge_state
from app.services.progress_evaluator import evaluate_mission_control


def test_eval_pauses_on_failed_status(base_state):
    state = merge_state(
        base_state,
        status=TaskStatus.FAILED.value,
        errors=["mission_act: planning API timeout"],
        mission={"kind": "writing", "budget": {"max_steps": 10}},
        progress={"steps_completed": 1},
        observation={"has_failures": True},
    )
    result = evaluate_mission_control(state)
    assert result.done is True
    assert result.action == "pause"
    assert "FAILED" in result.reason
