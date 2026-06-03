from app.runtime.state import merge_state
from app.services.mission_orchestrator import (
    append_work_items,
    build_next_lazy_work_item,
    get_current_work_item,
    mark_current_work_item_failed,
    work_plan_from_mission,
)
from app.services.mission_schema import build_mission_dict


def test_lazy_items_get_sequential_dependencies(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "step_policy": {"chars_per_step": 3000}}},
        kind="writing",
    )
    plan = work_plan_from_mission(mission)
    item1 = build_next_lazy_work_item(base_state, mission)
    plan = append_work_items(plan, [item1])
    state = merge_state(
        base_state,
        mission=mission,
        mission_step=1,
        progress={"work_plan": plan},
        manuscript={"body_bytes": 5000, "body_path": "novel.txt", "outline_bytes": 100, "outline_path": "o.md"},
    )
    item2 = build_next_lazy_work_item(merge_state(state, mission_step=2), mission)
    plan = append_work_items(plan, [item2])
    assert plan["items"][1].get("depends_on") == [plan["items"][0]["id"]]


def test_append_duplicate_lazy_id_no_self_dependency(base_state):
    item = {"id": "wi-step-2", "kind": "append_body", "status": "pending", "params": {}}
    plan = {"mode": "lazy", "items": [dict(item, status="done")]}
    plan = append_work_items(plan, [item])
    assert len(plan["items"]) == 1


def test_mark_current_work_item_failed_blocks_dependents(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "orchestration": {"enabled": True}}},
        kind="writing",
    )
    plan = {
        "mode": "explicit",
        "items": [
            {"id": "a", "kind": "write_outline", "status": "running", "depends_on": []},
            {"id": "b", "kind": "write_body", "status": "pending", "depends_on": ["a"]},
        ],
    }
    state = merge_state(
        base_state,
        mission=mission,
        progress={"work_plan": plan},
    )
    state = mark_current_work_item_failed(state, reason="test")
    wp = (state.get("progress") or {}).get("work_plan") or {}
    by_id = {i["id"]: i for i in wp.get("items") or []}
    assert by_id["a"]["status"] == "failed"
    assert by_id["b"]["status"] == "blocked"


def test_get_current_skips_blocked(base_state):
    mission = {"kind": "writing", "orchestration": {"enabled": True}}
    plan = {
        "items": [
            {"id": "a", "kind": "x", "status": "failed", "depends_on": []},
            {"id": "b", "kind": "y", "status": "blocked", "depends_on": ["a"]},
            {"id": "c", "kind": "z", "status": "pending", "depends_on": []},
        ]
    }
    state = merge_state(base_state, mission=mission, progress={"work_plan": plan})
    current = get_current_work_item(state)
    assert current is not None
    assert current["id"] == "c"
