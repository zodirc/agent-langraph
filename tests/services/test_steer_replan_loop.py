"""Steer replan must not re-enter planning LLM after contract is settled."""

from app.runtime.state import TaskStatus, merge_state
from app.services.mission_handoff import complete_mission_handoff
from app.services.mission_schema import build_mission_dict
from app.services.mission_steer import (
    consume_pending_steer,
    mission_must_run_planning,
    steer_replan_planning_satisfied,
    steer_requires_planning,
)


def test_steer_replan_planning_satisfied_after_contract(base_state):
    payload = {
        "steer_planning_done": True,
        "require_planning_after_steer": False,
        "turn_contract": {"primary_op": "write_outline", "forbid": []},
        "latest_steer_message": "使用原电影人物",
    }
    assert steer_replan_planning_satisfied(payload) is True
    assert steer_requires_planning(payload) is False
    assert mission_must_run_planning(merge_state(base_state, input_payload=payload)) is False


def test_consume_pending_skips_when_steer_already_planned(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing"}},
        kind="writing",
    )
    steer = "使用原电影人物，改动剧情走向"
    state = merge_state(
        base_state,
        mission=mission,
        input_payload={
            "steer_planning_done": True,
            "latest_steer_message": steer,
            "turn_contract": {"primary_op": "write_outline"},
        },
        pending_user_message={
            "messages": [{"message": steer, "goal_staged": True}],
        },
    )
    out = consume_pending_steer(state)
    assert out.get("pending_user_message") is None
    assert (out.get("input_payload") or {}).get("require_planning_after_steer") is not True


def test_handoff_materializes_writing_intent_after_steer_replan(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        mission_step=1,
        status=TaskStatus.MISSION_RUNNING.value,
        input_payload={
            "steer_planning_done": True,
            "turn_contract": {
                "primary_op": "write_outline",
                "forbid": [],
                "ops": [{"op": "write", "action": "write_outline"}],
            },
            "writing_intent": {
                "enabled": False,
                "source": "await_steer_planning",
            },
            "plan": ["contract: write_outline"],
        },
    )
    handed = complete_mission_handoff(state, state["input_payload"], source="test")
    intent = (handed.get("input_payload") or {}).get("writing_intent") or {}
    assert intent.get("enabled") is True
    assert str(intent.get("action") or "") == "write_outline"
