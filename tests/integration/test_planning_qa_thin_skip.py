"""Planning node skips full LLM for conversational QA in qa_mode."""

from unittest.mock import patch

from app.nodes.planning_node import planning_node
from app.runtime.state import TaskStatus, create_initial_state


@patch("app.services.llm_client.invoke_structured")
def test_planning_qa_thin_skip_avoids_planning_llm(mock_invoke, isolated_stores):
    mock_invoke.side_effect = AssertionError("full planning LLM must not run")

    state = create_initial_state(
        task_id="qa-thin-1",
        input_payload={"goal": "你好", "risk_level": "LOW"},
    )
    out = planning_node(state)
    mock_invoke.assert_not_called()

    payload = out.get("input_payload") or {}
    assert payload.get("target_mode") == "qa_mode"
    assert out.get("plan") == ["respond greeting"]
    assert out.get("skip_retrieval") is True
    assert out.get("status") == TaskStatus.PLANNED.value
    audits = [e for e in (out.get("audit_log") or []) if e.get("action") == "qa_thin_skip"]
    assert audits
