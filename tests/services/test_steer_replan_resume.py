"""Supersede replan dispatch must route to planner, not finalize→policy."""

from app.runtime.state import TaskStatus, merge_state
from app.services.mission_schema import build_mission_dict
from app.services.progress_evaluator import evaluate_mission_control


def test_evaluate_continues_on_supersede_replan_dispatch(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "orchestration": {"enabled": True}}},
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        status=TaskStatus.MISSION_RUNNING.value,
        current_node="mission_decide",
        interrupt_context={
            "control_state": "REPLANNING",
            "foreground_operation": {
                "kind": "supersede_with_input",
                "status": "replan_dispatching",
                "intent_revision": 3,
            },
        },
        input_payload={
            "foreground_preempt_consumed": True,
            "require_planning_after_steer": True,
            "foreground_replan_dispatch": True,
            "steer_planning_done": False,
            "intent_revision": 3,
            "goal": "使用原电影人物，改动剧情走向",
        },
    )
    result = evaluate_mission_control(state)
    assert result.done is False
    assert result.action == "continue"


def test_evaluate_pauses_when_supersede_queued_without_dispatch(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing"}},
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        status=TaskStatus.MISSION_PAUSED.value,
        current_node="mission_eval",
        interrupt_context={
            "control_state": "REPLANNING",
            "foreground_operation": {
                "kind": "supersede_with_input",
                "status": "replan_queued",
                "intent_revision": 2,
            },
        },
        input_payload={
            "foreground_preempt_consumed": True,
            "require_planning_after_steer": True,
            "steer_planning_done": False,
            "intent_revision": 2,
        },
    )
    result = evaluate_mission_control(state)
    assert result.done is True
    assert result.action == "pause"
    assert result.pause_reason == "superseded_by_new_input"
