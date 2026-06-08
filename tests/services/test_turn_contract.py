"""Turn contract: steer material change must block writing and route tools first."""

from app.runtime.state import merge_state
from app.runtime.planning_gate_router import route_after_incremental_planning
from app.runtime.router import route_after_tool
from app.services.mission_intervention import apply_planning_intervention
from app.services.mission_schema import resolve_writing_intent_for_step, build_mission_dict
from app.services.turn_contract import (
    augment_intervention_from_planning,
    build_turn_contract,
    contract_blocks_writing,
    finalize_turn_execution_plan,
)


def test_augment_intervention_from_read_edit_tools(base_state):
    payload = augment_intervention_from_planning(
        {
            "selected_tools": ["read_text_artifact", "edit_text_artifact"],
            "writing_intent": {"enabled": False, "action": "edit_plot"},
            "steer_intent_summary": "改越狱情节",
        },
        {"goal": "暴力越狱"},
        steer_planning_turn=True,
    )
    block = payload.get("mission_intervention") or {}
    assert block.get("action") == "edit_plot"
    assert block.get("force") is True
    assert (payload.get("writing_command") or {}).get("action") == "edit_plot"


def test_finalize_turn_execution_plan_blocks_append(base_state):
    mission = build_mission_dict(
        base_state,
        {
            "mission": {
                "kind": "writing",
                "total_target_chars": 100000,
                "step_policy": {"chars_per_step": 4000, "first_step": "outline", "then": "append_body"},
            }
        },
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        mission_step=3,
        manuscript={
            "body_path": "novel.txt",
            "body_bytes": 30000,
            "outline_path": "outline.txt",
            "outline_bytes": 5000,
        },
    )
    result = {
        "writing_intent": {"enabled": False, "action": "edit_plot"},
        "plan": ["read outline", "edit outline"],
        "skip_retrieval": True,
    }
    payload, exec_tools = finalize_turn_execution_plan(
        result,
        dict(state["input_payload"]),
        state,
        [],
        mission=mission,
        steer_planning_turn=True,
    )
    assert contract_blocks_writing(payload)
    assert (payload.get("writing_command") or {}).get("action") == "edit_plot"
    assert (payload.get("writing_intent_record") or {}).get("action") == "edit_plot"
    assert payload.get("writing_intent") is None


def test_resolve_writing_intent_honors_contract_over_step_policy(base_state):
    mission = build_mission_dict(
        base_state,
        {
            "mission": {
                "kind": "writing",
                "step_policy": {"chars_per_step": 4000, "then": "append_body"},
            }
        },
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        manuscript={"body_path": "novel.txt", "body_bytes": 8000, "outline_bytes": 2000},
        input_payload={
            "turn_contract": {
                "primary_op": "edit_plot",
                "override_step_policy": True,
                "forbid": ["append_body"],
                "tools": ["read_text_artifact", "edit_text_artifact"],
            },
            "writing_intent": {"enabled": False, "action": "edit_plot"},
        },
    )
    intent = resolve_writing_intent_for_step(state, mission=mission)
    assert intent.get("action") == "edit_plot"
    assert intent.get("enabled") is False


def test_route_after_planning_tools_before_writing_on_steer_contract(base_state):
    state = merge_state(
        base_state,
        plan=["read outline", "edit outline"],
        selected_tools=["read_text_artifact", "edit_text_artifact"],
        skip_retrieval=True,
        input_payload={
            **base_state["input_payload"],
            "turn_contract": {
                "primary_op": "edit_plot",
                "override_step_policy": True,
                "forbid": ["append_body"],
                "tools": ["read_text_artifact", "edit_text_artifact"],
            },
            "writing_intent": {"enabled": True, "action": "append_body"},
        },
    )
    assert contract_blocks_writing(state["input_payload"])
    assert route_after_incremental_planning(state) == "tool_execution"


def test_route_after_tool_skips_writing_when_contract_forbids(base_state):
    state = merge_state(
        base_state,
        input_payload={
            **base_state["input_payload"],
            "turn_contract": {
                "primary_op": "edit_plot",
                "override_step_policy": True,
                "forbid": ["append_body"],
            },
            "writing_intent": {"enabled": True, "action": "append_body"},
        },
    )
    assert route_after_tool(state) == "context_governance"


def test_apply_planning_intervention_sets_turn_contract(base_state):
    payload = apply_planning_intervention(
        {
            "mission_intervention": {
                "action": "edit_plot",
                "force": True,
                "intent_anchor": {"target_hint": "outline"},
            }
        },
        {"goal": "改设定"},
        state=base_state,
    )
    contract = payload.get("turn_contract") or {}
    assert contract.get("primary_op") == "edit_plot"
    assert "append_body" in (contract.get("forbid") or [])


def test_apply_turn_contract_binds_outline_tool_params(base_state):
    from app.services.turn_contract import apply_turn_contract_to_payload

    payload = apply_turn_contract_to_payload(
        {
            "writing_command": {
                "command_id": "cmd-1",
                "action": "edit_plot",
                "target_kind": "outline",
                "target_filename": "锚点纪元_大纲.txt",
                "edit_spec": {"filename": "锚点纪元_大纲.txt"},
            }
        },
        {
            "primary_op": "edit_plot",
            "override_step_policy": True,
            "forbid": ["append_body"],
            "tools": ["read_text_artifact", "edit_text_artifact"],
        },
    )
    assert payload.get("selected_tools") == ["read_text_artifact", "edit_text_artifact"]
    assert payload.get("tool_stages") == [["read_text_artifact"], ["edit_text_artifact"]]
    assert "read_text_artifact" not in (payload.get("tool_params") or {})


def test_build_turn_contract_from_explicit_block():
    contract = build_turn_contract(
        {
            "turn_contract": {
                "intent_kind": "forward_write",
                "primary_op": "append_body",
                "ops": [],
                "tools": [],
                "forbid": [],
            }
        },
        {},
        steer_planning_turn=False,
    )
    assert contract.get("primary_op") == "append_body"
    assert not contract.get("override_step_policy")
