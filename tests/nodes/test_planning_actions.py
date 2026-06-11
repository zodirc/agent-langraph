"""Planning node maps LLM planning output into the unified Action sequence (WP-3)."""

from __future__ import annotations

from unittest.mock import patch

from app.nodes.planning_node import planning_node
from app.runtime.state import TaskStatus, create_initial_state


def _planned(mock_invoke, result, goal="把大纲里的'地球'改成'火星'", task_id="plan-act-1"):
    mock_invoke.return_value = result
    state = create_initial_state(
        task_id=task_id,
        input_payload={"goal": goal, "risk_level": "LOW"},
    )
    with patch("app.nodes.planning_node.trace_enabled", return_value=False):
        return planning_node(state)


@patch("app.nodes.planning_node.invoke_structured")
def test_planning_maps_actions_to_planned_actions_and_transport(mock_invoke, isolated_stores):
    out = _planned(
        mock_invoke,
        {
            "plan": ["read outline", "edit outline"],
            "actions": [
                {"type": "read_artifact", "params": {"filename": "outline.txt"}},
                {
                    "type": "edit_artifact",
                    "params": {
                        "filename": "outline.txt",
                        "old_text": "地球",
                        "new_text": "火星",
                    },
                },
            ],
            "risk_level": "LOW",
            "skip_retrieval": True,
        },
    )
    mock_invoke.assert_called_once()
    assert out.get("status") == TaskStatus.PLANNED.value

    actions = out.get("planned_actions") or []
    assert [a["type"] for a in actions] == ["read_artifact", "edit_artifact"]
    assert actions[1]["params"]["old_text"] == "地球"

    # Transitional transport: actions rendered onto selected_tools/tool_params.
    assert out.get("selected_tools") == ["read_text_artifact", "edit_text_artifact"]
    tool_params = (out.get("input_payload") or {}).get("tool_params") or {}
    assert tool_params["edit_text_artifact"]["new_text"] == "火星"
    stages = (out.get("input_payload") or {}).get("tool_stages")
    assert stages == [["read_text_artifact"], ["edit_text_artifact"]]
    # Writing actions enable retrieval for style guidelines.
    assert out.get("skip_retrieval") is False
    assert (out.get("input_payload") or {}).get("writing_intent", {}).get("enabled") is True


@patch("app.nodes.planning_node.invoke_structured")
def test_planning_answer_only_actions_have_no_tools(mock_invoke, isolated_stores):
    out = _planned(
        mock_invoke,
        {
            "plan": ["answer directly"],
            "actions": [{"type": "answer", "params": {}, "completes_turn": True}],
            "risk_level": "LOW",
            "skip_retrieval": True,
        },
        goal="什么是光合作用？",
        task_id="plan-act-2",
    )
    actions = out.get("planned_actions") or []
    assert [a["type"] for a in actions] == ["answer"]
    assert out.get("selected_tools") == []
    assert (out.get("input_payload") or {}).get("writing_intent", {}).get("enabled") is False


@patch("app.nodes.planning_node.invoke_structured")
def test_planning_invalid_actions_are_skipped_not_fatal(mock_invoke, isolated_stores):
    out = _planned(
        mock_invoke,
        {
            "plan": ["do something"],
            "actions": [
                {"type": "frobnicate", "params": {}},
                {"type": "run_tool", "params": {"name": "echo", "message": "hi"}},
            ],
            "risk_level": "LOW",
            "skip_retrieval": True,
        },
        goal="把大纲里的'地球'改成'火星'，并回显确认",
        task_id="plan-act-3",
    )
    actions = out.get("planned_actions") or []
    assert [a["type"] for a in actions] == ["run_tool"]
    # qa_mode resident allowlist does not include echo; planned action remains.
    assert out.get("selected_tools") == []
    tool_params = (out.get("input_payload") or {}).get("tool_params") or {}
    assert "echo" not in tool_params


@patch("app.nodes.planning_node.invoke_structured")
def test_planning_retrieve_action_forces_retrieval(mock_invoke, isolated_stores):
    out = _planned(
        mock_invoke,
        {
            "plan": ["retrieve knowledge", "answer"],
            "actions": [
                {"type": "retrieve", "params": {"query": "长文写作规范"}},
                {"type": "answer", "params": {}},
            ],
            "risk_level": "LOW",
            "skip_retrieval": True,  # planner mistake: retrieve action wins
        },
        goal="根据知识库修订大纲第三章的设定",
        task_id="plan-act-4",
    )
    assert out.get("skip_retrieval") is False
