"""Mission wall-clock termination."""

from datetime import datetime, timedelta, timezone

from app.runtime.state import create_initial_state, merge_state
from app.services.progress_evaluator import evaluate_mission_control


def test_wall_clock_exceeded_pauses_mission():
    started = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    state = merge_state(
        create_initial_state(),
        mission={"budget": {"max_wall_sec": 60, "max_steps": 30, "max_failures": 3}},
        progress={"started_at": started, "steps_completed": 0, "consecutive_failures": 0},
        observation={"has_failures": False},
        mission_step=1,
    )
    result = evaluate_mission_control(state)
    assert result.done is True
    assert result.action == "pause"
    assert "wall_clock" in result.reason


def test_wall_clock_within_budget_continues():
    started = datetime.now(timezone.utc).isoformat()
    state = merge_state(
        create_initial_state(),
        mission={
            "budget": {"max_wall_sec": 3600, "max_steps": 30, "max_failures": 3},
            "constraints": {},
        },
        progress={"started_at": started, "steps_completed": 0, "consecutive_failures": 0},
        observation={"has_failures": False},
        mission_step=1,
    )
    result = evaluate_mission_control(state)
    assert result.done is False
    assert result.action == "continue"
