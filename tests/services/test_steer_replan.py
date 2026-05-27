"""Tests for steer replan service."""

from app.runtime.state import merge_state
from app.services.mission.steer_replan import apply_work_plan_patch, intervention_to_work_item


def test_intervention_to_work_item_rewrite():
    wi = intervention_to_work_item({"action": "rewrite_outline", "force": True}, step=3)
    assert wi is not None
    assert wi["kind"] == "write_outline"


def test_apply_work_plan_patch_cancels_append(base_state):
    state = merge_state(
        base_state,
        mission={"kind": "writing", "orchestration": {"enabled": True}},
        progress={
            "work_plan": {
                "mode": "lazy",
                "items": [
                    {"id": "wi-a", "kind": "append_chapter", "status": "pending", "title": "ch2"},
                    {"id": "wi-b", "kind": "append_chapter", "status": "done", "title": "ch1"},
                ],
            }
        },
        input_payload={
            "mission_intervention": {"action": "rewrite_outline", "force": True},
        },
    )
    updated = apply_work_plan_patch(state, {}, intervention={"action": "rewrite_outline", "force": True})
    items = updated["progress"]["work_plan"]["items"]
    cancelled = [i for i in items if i.get("status") == "cancelled"]
    assert any(i["id"] == "wi-a" for i in cancelled)
    prepended = [i for i in items if i.get("kind") == "write_outline" and i.get("status") == "pending"]
    assert prepended
    assert updated["input_payload"].get("steer_replan_impact")
