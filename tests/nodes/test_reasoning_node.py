from app.nodes.planning_node import planning_node
from app.nodes.reasoning_node import reasoning_node
from app.nodes.retrieval_node import retrieval_node
from app.nodes.tool_node import tool_execution_node
from app.runtime.state import TaskStatus, merge_state
from app.services.metrics_service import get_metrics_service


def test_reasoning_node_produces_result(base_state):
    state = tool_execution_node(retrieval_node(planning_node(base_state)))
    result = reasoning_node(state)
    assert result["status"] == TaskStatus.REASONED.value
    assert result["reasoning_result"] is not None
    assert "summary" in result["reasoning_result"]
    assert result["audit_log"][-1]["node"] == "reasoning"


def test_reasoning_node_turn_facts_digest(base_state):
    state = merge_state(
        base_state,
        tool_results=[
            {"tool": "calculator", "result": {"expression": "1+2", "result": "3"}},
        ],
        input_payload={
            **base_state["input_payload"],
            "goal": "1+2",
            "force_slow_reasoning": True,
        },
        memory_hits=[
            {
                "summary": "此前已经写过第一章。",
                "score": 0.9,
                "payload": {"chapter": 1},
            }
        ],
    )
    result = reasoning_node(state)
    assert result["status"] == TaskStatus.REASONED.value
    assert result.get("turn_facts") is not None
    structured = result["reasoning_result"]["structured"]
    assert "turn_facts_digest" in structured
    assert result["turn_facts"]["tools_executed"]


def test_reasoning_node_records_parser_fallback_metrics(base_state, monkeypatch):
    state = merge_state(
        base_state,
        input_payload={
            **base_state["input_payload"],
            "goal": "给我一段代码",
            "force_slow_reasoning": True,
        },
    )

    def _fake_invoke(*_args, **_kwargs):
        return {
            "summary": "```cpp\\nint main(){return 0;}\\n```",
            "confidence": 0.45,
            "risk_level": "MEDIUM",
            "structured": {"parser_fallback": True},
        }

    monkeypatch.setattr("app.nodes.reasoning_node.trace_enabled", lambda: False)
    monkeypatch.setattr("app.nodes.reasoning_node.answer_stream_enabled", lambda: False)
    monkeypatch.setattr("app.nodes.reasoning_node.invoke_structured", _fake_invoke)
    result = reasoning_node(state)
    assert result["status"] == TaskStatus.REASONED.value
    last_audit = result["audit_log"][-1]["detail"]
    assert last_audit["parser_fallback"] is True
    metrics = get_metrics_service().summary()["counters"]
    assert metrics["reasoning_parser_fallback"] >= 1
