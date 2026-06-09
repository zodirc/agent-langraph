"""COMPLETED + steer correction ingress and execution guard tests."""

from __future__ import annotations

from app.runtime.state import merge_state
from app.services.reasoning_execution_guard import (
    reasoning_claims_unexecuted_edit,
    reasoning_terminal_blocked_reason,
)
from app.services.turn_contract import (
    apply_turn_contract_to_payload,
    is_turn_contract_fulfilled,
    session_steer_correction_fallback_from_state,
)


def test_session_steer_correction_fallback_prefers_edit_not_write_outline(base_state):
    goal = "我认为，你应该使用原电影中的人物姓名"
    mission = {"kind": "writing", "objective": "写黑客帝国剧本"}
    ms = {
        "outline_path": "黑客帝国剧本_大纲.txt",
        "outline_bytes": 26000,
        "body_bytes": 0,
    }
    state = merge_state(
        base_state,
        mission=mission,
        manuscript=ms,
        input_payload={
            "goal": goal,
            "mission": mission,
            "turn_policy_decision": {
                "intent": "supersede_active_mission",
                "source": "completed_steer_correction",
            },
        },
    )
    fb = session_steer_correction_fallback_from_state(state)
    assert fb is not None
    assert fb.get("mission_intervention", {}).get("action") in ("edit_plot", "rewrite_outline")
    assert fb.get("writing_intent", {}).get("action") != "write_outline" or fb.get(
        "mission_intervention", {}
    ).get("action") == "rewrite_outline"


def test_write_outline_contract_unfulfilled_after_read_only(base_state):
    mission = {"kind": "writing", "objective": "写剧本"}
    payload = apply_turn_contract_to_payload(
        {
            "goal": "改人物名",
            "mission": mission,
            "selected_tools": ["read_text_artifact"],
        },
        {
            "primary_op": "write_outline",
            "tools": ["read_text_artifact"],
        },
    )
    state = merge_state(
        base_state,
        mission=mission,
        input_payload=payload,
        tool_results=[
            {
                "tool": "read_text_artifact",
                "status": "ok",
                "result": {"path": "outline.txt", "bytes": 100},
            }
        ],
    )
    assert not is_turn_contract_fulfilled(state)


def test_reasoning_claims_unexecuted_edit_detects_fence(base_state):
    summary = (
        "接下来将修改大纲。\n```edit\nedit_text_artifact: 林深 → Neo\n```"
    )
    assert reasoning_claims_unexecuted_edit(summary)


def test_reasoning_terminal_blocked_on_unfulfilled_contract(base_state):
    mission = {"kind": "writing", "objective": "写剧本"}
    payload = apply_turn_contract_to_payload(
        {"goal": "改人物名", "mission": mission},
        {
            "primary_op": "write_outline",
            "tools": ["read_text_artifact"],
        },
    )
    state = merge_state(
        base_state,
        mission=mission,
        input_payload=payload,
        tool_results=[
            {"tool": "read_text_artifact", "status": "ok", "result": {"path": "o.txt"}},
        ],
        reasoning_result={"summary": "done"},
    )
    assert reasoning_terminal_blocked_reason(state) is not None
