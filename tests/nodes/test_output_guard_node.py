from app.nodes.output_guard_node import output_guard_node
from app.runtime.state import TaskStatus, merge_state


def test_output_guard_skips_grounding_when_skip_retrieval_no_evidence(
    base_state, monkeypatch
):
    monkeypatch.setattr("app.nodes.output_guard_node.settings.OUTPUT_GUARD_ENABLED", True)
    monkeypatch.setattr(
        "app.nodes.output_guard_node.settings.RETRIEVAL_CITATION_CHECK_STRICTNESS", "basic"
    )
    monkeypatch.setattr(
        "app.nodes.output_guard_node.settings.RAG_FAITHFULNESS_CHECK_ENABLED", False
    )
    state = merge_state(
        base_state,
        skip_retrieval=True,
        reasoning_result={
            "summary": "你好！很高兴见到你。有什么我可以帮助你的吗？",
            "confidence": 1.0,
            "risk_level": "LOW",
        },
        status=TaskStatus.REASONED.value,
        input_payload={"target_mode": "qa_mode", "goal": "你好"},
    )
    result = output_guard_node(state)
    guard = result.get("output_guard_result") or {}
    assert guard.get("passed") is True
    assert guard.get("faithfulness", {}).get("skipped") is True
    assert guard.get("grounding_check") is None
    assert result.get("status") != TaskStatus.REJECTED.value


def test_output_guard_blocks_pii(base_state):
    state = merge_state(
        base_state,
        reasoning_result={
            "summary": "联系 admin@secret.com",
            "confidence": 0.9,
            "risk_level": "LOW",
        },
        status=TaskStatus.REASONED.value,
    )
    result = output_guard_node(state)
    assert result["output_guard_result"]["passed"] is False
    assert result["status"] == TaskStatus.REJECTED.value
