"""E2E: planning → tools → reasoning grounded on turn_facts."""

from app.nodes.planning_node import planning_node
from app.nodes.reasoning_node import reasoning_node
from app.nodes.retrieval_node import retrieval_node
from app.nodes.tool_node import tool_execution_node
from app.runtime.state import TaskStatus, merge_state


def test_calculator_e2e_turn_facts_and_reasoning(base_state):
    state = merge_state(
        base_state,
        input_payload={
            **base_state["input_payload"],
            "goal": "123+456是多少",
            "tool_params": {"calculator": {"expression": "123+456"}},
        },
    )
    state = planning_node(state)
    state = merge_state(
        state,
        selected_tools=["calculator"],
        input_payload={
            **state["input_payload"],
            "skip_retrieval": True,
        },
    )
    state = tool_execution_node(state)
    assert state["status"] == TaskStatus.TOOL_EXECUTED.value
    facts = state.get("turn_facts") or {}
    assert facts.get("tools_executed")
    assert any(
        t.get("output") == "579" or str(t.get("output")) == "579"
        for t in facts.get("tools_executed", [])
    )

    state = reasoning_node(state)
    assert state["status"] == TaskStatus.REASONED.value
    summary = state["reasoning_result"]["summary"]
    assert "579" in summary
    structured = state["reasoning_result"].get("structured") or {}
    assert structured.get("turn_facts_digest", {}).get("tool_count", 0) >= 1


def test_parallel_tools_via_stages(base_state):
    state = merge_state(
        base_state,
        selected_tools=["calculator", "get_runtime_info"],
        input_payload={
            **base_state["input_payload"],
            "goal": "123+456",
            "tool_stages": [["calculator", "get_runtime_info"]],
            "tool_params": {"calculator": {"expression": "123+456"}},
        },
    )
    result = tool_execution_node(state)
    assert result["status"] == TaskStatus.TOOL_EXECUTED.value
    tools_run = {r["tool"] for r in result["tool_results"]}
    assert tools_run == {"calculator", "get_runtime_info"}
    facts = result.get("turn_facts") or {}
    assert facts.get("tool_count") == 2
