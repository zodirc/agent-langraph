from app.runtime.state import create_initial_state, merge_state
from app.services.mission_schema import (
    build_mission_dict,
    coerce_step_policy,
    resolve_writing_intent_for_step,
    should_use_mission_runtime,
)


def test_should_use_mission_runtime_explicit_block():
    assert should_use_mission_runtime({"mission": {"kind": "writing"}})
    assert not should_use_mission_runtime({"goal": "写50万字小说"})


def test_should_use_mission_after_planning_auto_merge():
    payload = {
        "goal": "长篇",
        "mission": {
            "kind": "writing",
            "total_target_chars": 100000,
            "autonomous": True,
            "step_policy": {"chars_per_step": 4000},
        },
        "execution_mode": "mission",
    }
    assert should_use_mission_runtime(payload)


def test_build_mission_with_step_policy(base_state):
    payload = {
        "goal": "同人长篇",
        "mission": {
            "kind": "writing",
            "total_target_chars": 500000,
            "step_policy": {"chars_per_step": 3000, "first_step": "outline"},
            "autonomous": True,
        },
    }
    mission = build_mission_dict(base_state, payload, kind="writing")
    assert mission["success_criteria"]["target"] == 500000
    assert mission["step_policy"]["chars_per_step"] == 3000
    assert mission["execution_mode"] == "autonomous"


def test_resolve_writing_intent_outline_when_step_high(base_state):
    mission = build_mission_dict(
        base_state,
        {
            "mission": {
                "kind": "writing",
                "step_policy": {"first_step": "outline", "chars_per_step": 3000},
            },
        },
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        mission_step=12,
        manuscript={"body_bytes": 0, "outline_bytes": 0},
    )
    intent = resolve_writing_intent_for_step(state, mission=mission)
    assert intent["action"] == "write_outline"


def test_resolve_writing_intent_outline_first(base_state):
    mission = build_mission_dict(
        base_state,
        {
            "mission": {
                "kind": "writing",
                "total_target_chars": 10000,
                "step_policy": {"chars_per_step": 3000},
            },
        },
        kind="writing",
    )
    state = merge_state(base_state, mission=mission, manuscript={"body_bytes": 0})
    intent = resolve_writing_intent_for_step(state, mission=mission)
    assert intent["action"] == "write_outline"
    assert intent["target_chars"] >= 80
