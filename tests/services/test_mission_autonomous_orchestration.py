"""Autonomous orchestrated missions run step-to-step without stepwise pause."""

from datetime import datetime, timezone

from app.nodes.output_node import _resolve_output_status
from app.runtime.state import TaskStatus, merge_state
from app.services.mission_orchestrator import stepwise_pause
from app.services.mission_service import init_mission_state
from app.services.progress_evaluator import evaluate_mission_control


def test_stepwise_pause_off_for_autonomous():
    mission = {
        "kind": "writing",
        "execution_mode": "autonomous",
        "constraints": {"no_human": True},
        "orchestration": {"enabled": True},
    }
    assert stepwise_pause(mission) is False


def test_stepwise_pause_on_for_interactive_orchestration():
    mission = {
        "kind": "writing",
        "execution_mode": "interactive",
        "orchestration": {"enabled": True, "stepwise": True},
    }
    assert stepwise_pause(mission) is True


def test_evaluate_mission_control_autonomous_continues_after_step(base_state):
    mission = {
        "kind": "writing",
        "execution_mode": "autonomous",
        "constraints": {"no_human": True},
        "orchestration": {"enabled": True, "stepwise": False},
        "success_criteria": {
            "type": "metric_gte",
            "metric": "written_chars",
            "target": 1200000,
        },
        "budget": {"max_steps": 50, "max_wall_sec": 7200, "max_failures": 3},
    }
    state = merge_state(
        base_state,
        mission=mission,
        progress={
            "started_at": datetime.now(timezone.utc).isoformat(),
            "steps_completed": 1,
            "consecutive_failures": 0,
            "work_plan": {
                "mode": "lazy",
                "items": [{"id": "wi-1", "kind": "write_outline", "status": "done"}],
            },
        },
        mission_step=1,
        observation={"has_failures": False},
    )
    result = evaluate_mission_control(state)
    assert result.done is False
    assert result.action == "continue"


def test_init_autonomous_orchestration_stepwise_false(base_state):
    payload = {
        **base_state["input_payload"],
        "goal": "长篇",
        "mission": {
            "kind": "writing",
            "autonomous": True,
            "total_target_chars": 120000,
            "step_policy": {"chars_per_step": 4000},
        },
    }
    state = init_mission_state(base_state, payload)
    orch = (state.get("mission") or {}).get("orchestration") or {}
    assert state["mission"]["execution_mode"] == "autonomous"
    assert orch.get("stepwise") is False


def test_output_status_stays_paused_when_mission_control_pause(base_state):
    state = merge_state(
        base_state,
        status=TaskStatus.MISSION_PAUSED.value,
        mission={"kind": "writing", "orchestration": {"enabled": True}},
        mission_control={"done": True, "action": "pause", "reason": "stepwise"},
        progress={
            "work_plan": {
                "mode": "lazy",
                "items": [
                    {"id": "a", "status": "done"},
                    {"id": "b", "status": "pending"},
                ],
            },
            "metrics": {"written_chars": 100},
        },
    )
    assert _resolve_output_status(state) == TaskStatus.MISSION_PAUSED.value
