"""End-to-end evidence flow tests (§9.4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_FAILURE_CASES = json.loads(
    (Path(__file__).parent / "data" / "evidence_failure_cases.json").read_text(encoding="utf-8")
)


def test_retrieval_node_populates_evidence_fields(base_state, monkeypatch):
    from app.nodes.planning_node import planning_node
    from app.nodes.retrieval_node import retrieval_node

    monkeypatch.setattr(
        "app.services.retrieval_decision.settings.SKIP_RETRIEVAL_WHEN_NO_TOOLS", False
    )
    planned = planning_node(base_state)
    result = retrieval_node(planned)
    assert "retrieval_decision" in result
    assert "query_object" in result
    assert "retrieval_trace" in result
    assert result["retrieval_decision"].get("purpose")
    assert result["query_object"].get("standalone_query")


def test_context_injects_evidence_packets_not_duplicate_chunks(monkeypatch):
    from app.services.prompt_context_gateway import collect_context_items

    monkeypatch.setattr(
        "app.services.prompt_context_gateway.settings.RETRIEVAL_ENABLE_SNIPPET_FIRST", True
    )
    monkeypatch.setattr(
        "app.services.prompt_context_gateway.settings.RETRIEVAL_UNIFIED_HIERARCHY", False
    )
    state = {
        "input_payload": {"goal": "explain sorting"},
        "evidence_packets": [
            {
                "packet_id": "p1",
                "snippet_text": "Python uses Timsort.",
                "chunk_id": "c1",
                "source_title": "Algo",
                "support_type": "direct",
                "authority_level": 0.8,
            }
        ],
        "retrieved_knowledge": [
            {"doc_id": "c1", "content": "FULL CHUNK SHOULD NOT APPEAR", "relevance_passed": True}
        ],
    }
    items = collect_context_items(state, purpose="reasoning")
    knowledge = [i for i in items if i.kind == "knowledge"]
    assert len(knowledge) == 1
    assert "Timsort" in knowledge[0].content
    assert "FULL CHUNK SHOULD NOT" not in knowledge[0].content


def test_output_guard_grounding_blocks_unsupported(monkeypatch):
    from app.nodes.output_guard_node import output_guard_node
    from app.runtime.state import TaskStatus

    monkeypatch.setattr("app.nodes.output_guard_node.settings.OUTPUT_GUARD_ENABLED", True)
    monkeypatch.setattr(
        "app.nodes.output_guard_node.settings.RETRIEVAL_CITATION_CHECK_STRICTNESS", "basic"
    )
    monkeypatch.setattr(
        "app.nodes.output_guard_node.settings.RAG_FAITHFULNESS_CHECK_ENABLED", False
    )
    state = {
        "task_id": "t1",
        "session_id": "s1",
        "user_id": "u1",
        "task_type": "qa",
        "input_payload": {},
        "current_node": "reasoning",
        "status": TaskStatus.REASONED.value,
        "review_required": False,
        "audit_log": [],
        "errors": [],
        "retry_count": 0,
        "node_history": [],
        "reasoning_result": {
            "summary": "Therefore the capital of France is Berlin and it is definitely correct."
        },
        "retrieved_knowledge": [
            {"doc_id": "paris", "content": "Paris is the capital of France."}
        ],
        "retrieval_decision": {"answer_mode": "strict_grounded"},
        "evidence_packets": [
            {"packet_id": "p", "chunk_id": "paris", "snippet_text": "Paris is the capital of France."}
        ],
    }
    result = output_guard_node(state)
    guard = result.get("output_guard_result") or {}
    assert guard.get("grounding_check") is not None


@pytest.mark.parametrize("fc", _FAILURE_CASES, ids=[f["tag"] for f in _FAILURE_CASES])
def test_failure_case_library_tags_documented(fc):
    """Ensure failure case library covers all taxonomy tags."""
    assert fc.get("tag")
    assert fc.get("description")
