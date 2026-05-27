from app.services.mission_routing import (
    apply_planning_mission_decision,
    explicit_mission_requested,
    mission_auto_allowed,
    normalize_planning_mission,
    should_use_mission_runtime,
)


def test_explicit_mission_from_ui_block():
    payload = {"execution_mode": "mission", "mission": {"kind": "writing", "total_target_chars": 100000}}
    assert explicit_mission_requested(payload)
    assert should_use_mission_runtime(payload)


def test_goal_only_not_mission():
    assert not should_use_mission_runtime({"goal": "写50万字小说", "mission_auto": True})


def test_planning_auto_mission_recommended(test_settings):
    result = {
        "plan": ["outline", "append"],
        "mission_recommended": True,
        "total_target_chars": 120000,
        "writing_intent": {"enabled": False},
        "risk_level": "LOW",
    }
    payload = {"goal": "写一部长篇同人", "mission_auto": True}
    block = normalize_planning_mission(result, payload)
    assert block is not None
    assert block["total_target_chars"] == 120000
    assert block["autonomous"] is True

    updated, reason = apply_planning_mission_decision(result, payload)
    assert reason == "planning_auto_mission"
    assert should_use_mission_runtime(updated)


def test_planning_auto_disabled_by_flag(test_settings):
    result = {
        "mission_recommended": True,
        "total_target_chars": 200000,
        "mission": {"kind": "writing", "total_target_chars": 200000},
    }
    payload = {"goal": "长篇", "mission_auto": False}
    assert not mission_auto_allowed(payload)
    updated, reason = apply_planning_mission_decision(result, payload)
    assert reason is None
    assert "mission" not in updated


def test_explicit_wins_over_auto_off(test_settings):
    payload = {
        "goal": "写书",
        "execution_mode": "mission",
        "mission": {"kind": "writing", "total_target_chars": 80000},
    }
    result = {"mission_recommended": False}
    updated, reason = apply_planning_mission_decision(result, payload)
    assert reason is None
    assert should_use_mission_runtime(updated)


def test_mission_block_from_planning_without_flag(test_settings):
    result = {
        "plan": ["mission writing"],
        "mission": {
            "kind": "writing",
            "total_target_chars": 600000,
            "step_policy": {"chars_per_step": 3000},
        },
    }
    payload = {"goal": "同人长篇"}
    block = normalize_planning_mission(result, payload)
    assert block["total_target_chars"] == 600000
