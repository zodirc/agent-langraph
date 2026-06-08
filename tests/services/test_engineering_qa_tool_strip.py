"""Engineering UI + conversational goal must not leave engineering tools selected."""

from unittest.mock import patch

from app.nodes.planning_node import planning_node
from app.runtime.planning_gate_router import route_after_incremental_planning
from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.mode_execution import apply_qa_mode_contract


def test_apply_qa_mode_contract_strips_engineering_tools():
    state = merge_state(
        create_initial_state(
            task_id="qa-strip-1",
            input_payload={"goal": "你好", "writing_intent": {"enabled": False}},
        ),
        selected_tools=["mkdir_path", "read_file", "verify_backend", "write_file"],
    )
    payload = dict(state.get("input_payload") or {})
    audit: dict = {}
    intent: dict = {}
    tools = list(state.get("selected_tools") or [])
    payload, audit, tools, state = apply_qa_mode_contract(
        state, payload, audit, tools, intent
    )
    assert tools == []
    assert payload.get("tool_params") in (None, {})


@patch("app.services.llm_client.invoke_structured")
def test_planning_engineering_greeting_routes_to_reasoning(mock_invoke, isolated_stores):
    mock_invoke.side_effect = AssertionError("full planning LLM must not run")

    state = create_initial_state(
        task_id="eng-hi-1",
        input_payload={
            "goal": "你好",
            "interaction_mode": "engineering",
            "risk_level": "LOW",
        },
    )
    out = planning_node(state)
    mock_invoke.assert_not_called()

    payload = out.get("input_payload") or {}
    assert payload.get("target_mode") == "qa_mode"
    tools = out.get("selected_tools") or []
    assert "mkdir_path" not in tools
    assert "write_file" not in tools
    assert "verify_backend" not in tools
    assert out.get("status") == TaskStatus.PLANNED.value
    assert route_after_incremental_planning(out) in ("context_governance", "retrieval", "tool_execution")
