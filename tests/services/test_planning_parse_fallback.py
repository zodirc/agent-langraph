"""Planning JSON parse resilience: fallback and empty-stream recovery."""

from app.runtime.state import merge_state
from app.services import llm_client
from app.services.mission_steer import apply_steer_planning_gate
from app.services.turn_contract import (
    planning_fallback_from_state,
    steer_replan_planning_fallback_from_state,
)

STEER = "我认为你需要使用原电影的人物，只是在一些原电影的剧情走向上改动，也不需要架空人物"


def test_planning_fallback_edit_plot_queue(base_state):
    state = merge_state(
        base_state,
        mission={"kind": "writing", "step_policy": {"then": "append_body"}},
        manuscript={"outline_path": "outline.txt", "outline_bytes": 1200},
        input_payload={
            **base_state["input_payload"],
            "goal": "提升主角出身与能力",
        },
        progress={
            "work_plan": {
                "items": [
                    {"id": "wi-1", "kind": "edit_plot", "status": "pending", "title": "edit plot"},
                ]
            }
        },
    )
    fb = planning_fallback_from_state(state)
    assert fb is not None
    assert fb.get("mission_intervention", {}).get("action") == "edit_plot"
    anchor = fb.get("mission_intervention", {}).get("intent_anchor") or {}
    assert anchor.get("target_hint") == "outline"


def test_extract_json_with_repair_planning_state_fallback(base_state):
    state = merge_state(
        base_state,
        mission={"kind": "writing"},
        manuscript={"outline_bytes": 500, "outline_path": "outline.txt"},
        input_payload={**base_state["input_payload"], "goal": "改设定"},
        progress={
            "work_plan": {
                "items": [{"kind": "edit_plot", "status": "pending"}],
            }
        },
    )

    original = llm_client._extract_json
    llm_client._extract_json = lambda *a, **k: (_ for _ in ()).throw(ValueError("bad"))
    try:
        result = llm_client.extract_json_with_repair(
            "planning",
            "",
            prefer_keys=("plan",),
            trace_state=state,
        )
    finally:
        llm_client._extract_json = original

    assert result.get("parser_fallback") is True
    assert result.get("mission_intervention", {}).get("action") == "edit_plot"


def test_steer_replan_fallback_edit_plot_when_outline_exists(base_state):
    mission = {"kind": "writing", "step_policy": {"then": "append_body"}}
    payload = apply_steer_planning_gate(
        {
            "goal": STEER,
            "latest_steer_message": STEER,
            "steer_applied_at": "2026-06-05T00:00:00Z",
            "mission": mission,
            "foreground_replan_dispatch": True,
        }
    )
    state = merge_state(
        base_state,
        mission=mission,
        manuscript={"outline_path": "暗战_大纲.txt", "outline_bytes": 12000},
        input_payload=payload,
    )
    fb = steer_replan_planning_fallback_from_state(state)
    assert fb is not None
    assert fb.get("fallback_reason") == "steer_replan_edit_plot"
    assert fb.get("mission_intervention", {}).get("action") == "edit_plot"
    assert fb.get("writing_intent", {}).get("action") == "edit_plot"
    patch = fb.get("work_plan_patch") or {}
    assert "write_outline" in (patch.get("cancel_kinds") or [])
    assert fb.get("steer_outline_route") == "modify"


def test_steer_replan_fallback_write_outline_when_no_outline_file(base_state):
    mission = {"kind": "writing", "step_policy": {"then": "append_body"}}
    payload = apply_steer_planning_gate(
        {
            "goal": STEER,
            "latest_steer_message": STEER,
            "steer_applied_at": "2026-06-05T00:00:00Z",
            "mission": mission,
            "foreground_replan_dispatch": True,
        }
    )
    state = merge_state(
        base_state,
        mission=mission,
        manuscript={"body_path": "暗战.txt", "body_bytes": 0},
        input_payload=payload,
    )
    fb = steer_replan_planning_fallback_from_state(state)
    assert fb is not None
    assert fb.get("fallback_reason") == "steer_replan_write_outline"
    assert fb.get("steer_outline_route") == "rewrite"
    assert fb.get("writing_intent", {}).get("action") == "write_outline"
    assert fb.get("mission_intervention", {}).get("action") == "rewrite_outline"


def test_apply_steer_replan_outline_route_clears_read_only_tools(base_state):
    from app.services.turn_contract import apply_steer_replan_outline_route
    from app.services.mission_steer import apply_steer_planning_gate

    mission = {"kind": "writing"}
    payload = apply_steer_planning_gate(
        {
            "goal": STEER,
            "latest_steer_message": STEER,
            "foreground_replan_dispatch": True,
            "mission": mission,
        }
    )
    state = merge_state(
        base_state,
        mission=mission,
        manuscript={"outline_path": "暗流涌动_大纲.txt", "outline_bytes": 500},
        input_payload=payload,
    )
    llm_read_loop = {
        "plan": ["read outline"],
        "selected_tools": ["read_text_artifact"],
        "tool_stages": [["read_text_artifact"]],
    }
    routed = apply_steer_replan_outline_route(state, llm_read_loop)
    assert routed.get("steer_outline_route") == "modify"
    assert routed.get("selected_tools") == []
    assert "tool_stages" not in routed


def test_apply_steer_replan_outline_route_overrides_llm_edit_when_no_file(base_state):
    from app.services.turn_contract import apply_steer_replan_outline_route

    mission = {"kind": "writing"}
    payload = apply_steer_planning_gate(
        {
            "goal": STEER,
            "latest_steer_message": STEER,
            "foreground_replan_dispatch": True,
            "mission": mission,
        }
    )
    state = merge_state(
        base_state,
        mission=mission,
        manuscript={},
        input_payload=payload,
    )
    llm_wrong = {
        "plan": ["edit plot"],
        "writing_intent": {"enabled": False, "action": "edit_plot"},
        "mission_intervention": {"action": "edit_plot", "force": True},
    }
    routed = apply_steer_replan_outline_route(state, llm_wrong)
    assert routed.get("steer_outline_route") == "rewrite"
    assert routed.get("writing_intent", {}).get("action") == "write_outline"
