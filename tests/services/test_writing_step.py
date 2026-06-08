"""Mission writing step: advance_writing orchestration vs executor ops."""

from app.nodes.writing_node import writing_node
from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.turn_contract import build_turn_contract, finalize_turn_execution_plan
from app.services.writing_step import (
    ADVANCE_WRITING_PRIMARY_OP,
    coerce_writing_action_for_manuscript_state,
    materialize_writing_step_intent,
    normalize_writing_mission,
)


def test_normalize_writing_mission_sets_kind():
    block = {"total_target_chars": 50000, "step_policy": {"then": "append_body"}}
    out = normalize_writing_mission(block)
    assert out is not None
    assert out["kind"] == "writing"


def test_finalize_ignores_llm_append_body_when_mission_present(base_state):
    mission = {
        "kind": "writing",
        "total_target_chars": 50000,
        "step_policy": {
            "first_step": "outline",
            "then": "append_body",
            "chars_per_step": 4000,
            "body_artifact": "暗流涌动.txt",
            "outline_artifact": "暗流涌动_大纲.txt",
        },
        "autonomous": True,
    }
    payload = {
        **base_state["input_payload"],
        "mission": mission,
        "goal": "写一篇悬疑小说，民国背景，10章",
    }
    result = {
        "plan": ["contract: reasoning"],
        "writing_action": "append_body",
        "writing_intent": {"enabled": True, "action": "append_body"},
        "selected_tools": [],
        "risk_level": "LOW",
        "skip_retrieval": False,
    }
    state = merge_state(base_state, input_payload=payload, manuscript={"body_bytes": 0})
    payload_out, _ = finalize_turn_execution_plan(
        result,
        payload,
        state,
        [],
        mission=mission,
        steer_planning_turn=False,
    )
    intent = payload_out.get("writing_intent") or {}
    assert intent.get("enabled") is True
    assert intent.get("action") == "write_outline"
    contract = payload_out.get("turn_contract") or {}
    assert contract.get("primary_op") == ADVANCE_WRITING_PRIMARY_OP


def test_build_turn_contract_mission_uses_advance_writing(base_state):
    payload = {
        **base_state["input_payload"],
        "mission": {
            "kind": "writing",
            "total_target_chars": 40000,
            "step_policy": {"first_step": "outline", "then": "append_body"},
        },
    }
    result = {
        "writing_action": "append_body",
        "writing_intent": {"enabled": True, "action": "append_body"},
        "selected_tools": [],
    }
    contract = build_turn_contract(result, payload, steer_planning_turn=False)
    assert contract.get("primary_op") == ADVANCE_WRITING_PRIMARY_OP


def test_coerce_append_to_write_outline_on_empty_disk(base_state, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "coerce-empty-disk"
    state = merge_state(
        base_state,
        task_id=task_id,
        manuscript={"body_bytes": 0, "outline_bytes": 0},
    )
    assert coerce_writing_action_for_manuscript_state(state, "append_body") == "write_outline"


def test_materialize_writing_step_outline_first(base_state):
    mission = {
        "kind": "writing",
        "step_policy": {"first_step": "outline", "then": "append_body", "chars_per_step": 3000},
    }
    state = merge_state(base_state, mission=mission, manuscript={"body_bytes": 0})
    intent = materialize_writing_step_intent(state, mission)
    assert intent["action"] == "write_outline"
    assert intent["enabled"] is True


def test_writing_node_coerces_append_to_outline(base_state, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    state = merge_state(
        base_state,
        input_payload={
            **base_state["input_payload"],
            "goal": "写一篇悬疑小说",
            "writing_intent": {
                "enabled": True,
                "action": "append_body",
                "target_chars": 500,
                "min_chars": 50,
            },
        },
        manuscript={"body_bytes": 0, "outline_bytes": 0},
    )
    fake_outline = "# 大纲\n\n" + ("第一章 谍影\n" * 30)

    from unittest.mock import patch

    with patch(
        "app.nodes.writing_node._generate_validated_content",
        return_value=fake_outline,
    ):
        result = writing_node(state)

    assert result["status"] == TaskStatus.WRITTEN.value
    tools = [t.get("tool") for t in (result.get("tool_results") or [])]
    assert "write_text_artifact" in tools
