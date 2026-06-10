from unittest.mock import patch

from app.nodes.tool_node import tool_execution_node
from app.runtime.state import TaskStatus, merge_state
from app.services.artifact_tools import handle_write_text_artifact


def test_tool_node_runs_calculator(base_state):
    state = merge_state(
        base_state,
        selected_tools=["calculator"],
        input_payload={
            **base_state["input_payload"],
            "goal": "123+456是多少",
        },
    )
    result = tool_execution_node(state)
    assert result["status"] == TaskStatus.TOOL_EXECUTED.value
    assert result["tool_results"][0]["result"]["result"] == "579"


def test_tool_node_runs_get_runtime_info(base_state):
    state = merge_state(
        base_state,
        selected_tools=["get_runtime_info"],
        input_payload={**base_state["input_payload"], "goal": "你是什么模型"},
    )
    result = tool_execution_node(state)
    assert result["tool_results"][0]["result"]["web_search"] is False
    assert "model_name" in result["tool_results"][0]["result"]


def test_tool_node_runs_artifact_tools_inline(base_state):
    """Unified core: artifact writes execute in tool_execution (no writing subgraph)."""
    state = merge_state(
        base_state,
        selected_tools=["write_text_artifact", "calculator"],
        input_payload={
            **base_state["input_payload"],
            "goal": "123+456",
            "tool_params": {
                "calculator": {"expression": "123+456"},
                "write_text_artifact": {"filename": "out.txt", "content": "579"},
            },
        },
    )
    result = tool_execution_node(state)
    assert result["status"] == TaskStatus.TOOL_EXECUTED.value
    tools_run = [r["tool"] for r in result["tool_results"]]
    assert "write_text_artifact" in tools_run
    assert "calculator" in tools_run
    assert result.get("turn_facts") is not None


def test_tool_node_executes_planned_actions(base_state):
    """planned_actions present → unified action executor path."""
    handle_write_text_artifact(
        {
            "task_id": base_state["task_id"],
            "filename": "draft.txt",
            "content": "hello world hello",
        }
    )
    state = merge_state(
        base_state,
        planned_actions=[
            {
                "type": "edit_artifact",
                "params": {
                    "filename": "draft.txt",
                    "old_text": "hello",
                    "new_text": "hi",
                    "replace_all": True,
                },
            }
        ],
    )
    result = tool_execution_node(state)
    assert result["status"] == TaskStatus.TOOL_EXECUTED.value
    edits = [r for r in result["tool_results"] if r.get("tool") == "edit_text_artifact"]
    assert edits and edits[0].get("status") == "ok"
    assert result["turn_facts"].get("edit_applied") is True


def test_tool_node_planned_action_zero_replacement_not_applied(base_state):
    """Edit honesty: 0 replacements → turn_facts.edit_applied is False."""
    handle_write_text_artifact(
        {
            "task_id": base_state["task_id"],
            "filename": "draft2.txt",
            "content": "hello world",
        }
    )
    state = merge_state(
        base_state,
        planned_actions=[
            {
                "type": "edit_artifact",
                "params": {
                    "filename": "draft2.txt",
                    "old_text": "absent text",
                    "new_text": "hi",
                },
            }
        ],
    )
    result = tool_execution_node(state)
    assert result["turn_facts"].get("edit_applied") is False


def test_missing_artifact_is_non_retryable(base_state):
    state = merge_state(
        base_state,
        retry_count=2,
        selected_tools=["read_text_artifact"],
        input_payload={
            **base_state["input_payload"],
            "tool_params": {"read_text_artifact": {"filename": "missing-output.md"}},
        },
    )
    result = tool_execution_node(state)
    assert result["status"] == TaskStatus.TOOL_FAILED.value
    assert result["retry_count"] == 2
    assert result["audit_log"][-1]["action"] == "non_retryable_error"
