"""Steer replan work_plan supersede on foreground preempt."""

from app.runtime.state import merge_state
from app.services.foreground_execution import enter_replanning_state
from app.services.mission.steer_replan import supersede_pending_work_plan
from app.services.mission_schema import build_mission_dict


def test_supersede_pending_work_plan_cancels_stale_items(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "orchestration": {"enabled": True}}},
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        progress={
            "work_plan": {
                "mode": "lazy",
                "items": [
                    {"id": "wi-1", "kind": "write_outline", "status": "pending"},
                    {"id": "wi-2", "kind": "append_body", "status": "pending"},
                ],
                "current_id": "wi-1",
            }
        },
        input_payload={"current_work_item": {"id": "wi-1", "kind": "write_outline"}},
    )
    out = supersede_pending_work_plan(state)
    items = (out.get("progress") or {}).get("work_plan", {}).get("items") or []
    assert all(str(i.get("status") or "") != "pending" for i in items)
    assert not (out.get("input_payload") or {}).get("current_work_item")


def test_enter_replanning_supersedes_work_plan(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing"}},
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        progress={
            "work_plan": {
                "items": [{"id": "wi-o", "kind": "write_outline", "status": "pending"}],
                "current_id": "wi-o",
            }
        },
        input_payload={"require_planning_after_steer": True},
    )
    out = enter_replanning_state(state, action_hint="rewrite")
    items = (out.get("progress") or {}).get("work_plan", {}).get("items") or []
    assert items[0].get("status") == "superseded"
