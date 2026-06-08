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


def test_tool_node_skips_writing_tools(base_state):
    """Write/append tools run in writing_node, not tool_execution."""
    state = merge_state(
        base_state,
        selected_tools=["write_text_artifact", "calculator"],
        input_payload={
            **base_state["input_payload"],
            "goal": "123+456",
            "tool_params": {"calculator": {"expression": "123+456"}},
        },
    )
    result = tool_execution_node(state)
    assert result["status"] == TaskStatus.TOOL_EXECUTED.value
    tools_run = [r["tool"] for r in result["tool_results"]]
    assert "write_text_artifact" not in tools_run
    assert "calculator" in tools_run
    assert result.get("turn_facts") is not None


def test_read_text_artifact_prefers_manuscript_pointer(base_state):
    handle_write_text_artifact(
        {
            "task_id": base_state["task_id"],
            "filename": "novel.txt",
            "content": "chapter content",
        }
    )
    state = merge_state(
        base_state,
        selected_tools=["read_text_artifact"],
        manuscript={"body_path": "novel.txt", "body_bytes": 15},
        input_payload={
            **base_state["input_payload"],
            "tool_params": {"read_text_artifact": {}},
        },
    )
    result = tool_execution_node(state)
    assert result["status"] == TaskStatus.TOOL_EXECUTED.value
    read_result = result["tool_results"][0]["result"]
    assert read_result["filename"] == "novel.txt"
    assert "chapter content" in read_result["content"]


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
