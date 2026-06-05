from app.services.prompt_context_gateway import collect_context_items
from app.services.relevance_gate import (
    apply_relevance_gate,
    extract_evidence_snippet,
    should_inject_knowledge,
)


def test_apply_relevance_gate_filters_low_scores(monkeypatch):
    monkeypatch.setattr("app.services.relevance_gate.settings.RAG_RELEVANCE_GATE_ENABLED", True)
    monkeypatch.setattr("app.services.relevance_gate.settings.RAG_RERANK_MIN_SCORE", 0.5)
    monkeypatch.setattr("app.services.relevance_gate.settings.RAG_MIN_RELEVANT_HITS", 1)
    monkeypatch.setattr("app.services.relevance_gate.settings.RAG_RERANK_BACKEND", "cross_encoder")
    hits = [
        {"doc_id": "a", "rerank_score": 0.9},
        {"doc_id": "b", "rerank_score": 0.1},
        {"doc_id": "c", "rerank_score": 0.2},
    ]
    result = apply_relevance_gate(hits, "query")
    ids = [h["doc_id"] for h in result]
    assert "a" in ids
    assert "b" not in ids
    assert all(h.get("relevance_passed") for h in result)


def test_apply_relevance_gate_fallback_min_hits(monkeypatch):
    monkeypatch.setattr("app.services.relevance_gate.settings.RAG_RELEVANCE_GATE_ENABLED", True)
    monkeypatch.setattr("app.services.relevance_gate.settings.RAG_RERANK_MIN_SCORE", 0.9)
    monkeypatch.setattr("app.services.relevance_gate.settings.RAG_MIN_RELEVANT_HITS", 1)
    monkeypatch.setattr("app.services.relevance_gate.settings.RAG_RERANK_BACKEND", "cross_encoder")
    hits = [
        {"doc_id": "a", "rerank_score": 0.2},
        {"doc_id": "b", "rerank_score": 0.1},
    ]
    result = apply_relevance_gate(hits, "query")
    assert len(result) == 1
    assert result[0]["relevance_reason"] == "fallback_min_hits"


def test_extract_evidence_snippet_keeps_relevant_sentences(monkeypatch):
    monkeypatch.setattr("app.services.relevance_gate.settings.RAG_SNIPPET_ENABLED", True)
    monkeypatch.setattr("app.services.relevance_gate.settings.RAG_SNIPPET_MAX_SENTENCES", 2)
    text = (
        "无关的背景介绍段落。Python 排序算法使用 Timsort 实现。"
        "另一段无关内容。list.sort 是原地排序方法。"
        "结尾无关句。"
    )
    snippet = extract_evidence_snippet(text, "Python sort", title="Algo")
    assert "Python" in snippet or "sort" in snippet
    assert len(snippet) < len(text)


def test_should_inject_knowledge_reasoning_blocks_adjacency():
    doc = {"doc_id": "x", "relevance_passed": True, "source": "adjacency", "adjacency_only": True}
    inject, priority, droppable = should_inject_knowledge(doc, purpose="reasoning")
    assert inject is False
    assert droppable is True


def test_collect_context_items_skips_failed_reasoning_hits():
    state = {
        "input_payload": {"goal": "explain Python sorting"},
        "retrieved_knowledge": [
            {
                "doc_id": "good",
                "content": "Python sort uses Timsort.",
                "relevance_passed": True,
                "relevance_score": 0.8,
            },
            {
                "doc_id": "bad",
                "content": "unrelated noise",
                "relevance_passed": False,
                "relevance_score": 0.1,
            },
        ],
    }
    items = collect_context_items(state, purpose="reasoning")
    doc_ids = [i.meta.get("doc_id") for i in items if i.kind == "knowledge"]
    assert "good" in doc_ids
    assert "bad" not in doc_ids


def test_collect_context_items_skips_knowledge_for_intent_observation():
    state = {
        "input_payload": {"goal": "hi"},
        "retrieved_knowledge": [
            {"doc_id": "x", "content": "some doc", "relevance_passed": True},
        ],
    }
    items = collect_context_items(state, purpose="intent_observation")
    assert not any(i.kind == "knowledge" for i in items)
