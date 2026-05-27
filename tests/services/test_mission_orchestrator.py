from app.services.mission_intervention import apply_intervention_to_payload
from app.services.mission_orchestrator import (
    build_next_lazy_work_item,
    ensure_work_plan,
    get_current_work_item,
    orchestration_enabled,
    work_plan_from_mission,
)
from app.services.mission_steer import apply_steer_message
from app.runtime.state import create_initial_state, merge_state
from app.services.mission_schema import build_mission_dict


def test_orchestration_enabled_for_long_writing():
    mission = {
        "kind": "writing",
        "success_criteria": {"type": "metric_gte", "metric": "written_chars", "target": 50000},
        "step_policy": {"chars_per_step": 4000},
    }
    assert orchestration_enabled(mission) is True


def test_lazy_plan_starts_empty():
    mission = {"kind": "writing", "orchestration": {"enabled": True}}
    plan = work_plan_from_mission(mission)
    assert plan["mode"] == "lazy"
    assert plan["items"] == []


def test_build_next_lazy_work_item_outline(base_state):
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
    state = merge_state(base_state, mission=mission, manuscript={"body_bytes": 0})
    item = build_next_lazy_work_item(state, mission)
    assert item is not None
    assert item["kind"] == "write_outline"


def test_ensure_work_plan_lazy(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 12000}},
        kind="writing",
    )
    mission = {**mission, "orchestration": {"enabled": True, "stepwise": True}}
    state = merge_state(base_state, mission=mission, progress={})
    state = ensure_work_plan(state)
    plan = (state.get("progress") or {}).get("work_plan") or {}
    assert plan.get("mode") == "lazy"


def test_steer_forced_edit_inserts_work_item(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 12000}},
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission={**mission, "orchestration": {"enabled": True, "stepwise": True}},
        progress=work_plan_from_mission(mission),
        status="MISSION_PAUSED",
    )
    updated = apply_steer_message(
        state,
        "",
        intervention={
            "action": "edit_plot",
            "force": True,
            "edit_spec": {
                "filename": "novel.txt",
                "old_text": "旧台词",
                "new_text": "新台词",
            },
        },
    )
    items = (updated.get("progress") or {}).get("work_plan", {}).get("items") or []
    assert any(i.get("kind") == "edit_plot" for i in items)
