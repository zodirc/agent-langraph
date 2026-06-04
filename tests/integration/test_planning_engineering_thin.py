"""Planning node skips full LLM when pre_planning resolved engineering_mode."""

from unittest.mock import patch

from app.nodes.planning_node import planning_node
from app.runtime.state import TaskStatus, create_initial_state


@patch("app.services.llm_client.invoke_structured")
def test_planning_engineering_thin_skip_avoids_planning_llm(mock_invoke, isolated_stores):
    mock_invoke.side_effect = AssertionError("full planning LLM must not run")

    state = create_initial_state(
        task_id="thin-plan-1",
        input_payload={
            "goal": "做一个浏览器 2048 小游戏，落盘到 games 目录",
            "interaction_mode": "engineering",
            "risk_level": "LOW",
        },
    )
    out = planning_node(state)
    mock_invoke.assert_not_called()

    payload = out.get("input_payload") or {}
    assert payload.get("target_mode") == "engineering_mode"
    assert out.get("status") == TaskStatus.PLANNED.value
    audits = [e for e in (out.get("audit_log") or []) if e.get("action") == "engineering_thin_skip"]
    assert audits
