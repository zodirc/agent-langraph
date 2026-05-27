"""RAG pipeline eval: recall proxy on keyword store."""

from __future__ import annotations

from tests.eval.eval_metrics import record


def test_rag_recall_proxy(isolated_stores, test_settings):
    from app.services.knowledge_store import get_knowledge_store

    store = get_knowledge_store()
    doc_id = store.upsert_document(
        "LangGraph",
        "LangGraph is a framework for building stateful agent workflows.",
        metadata={"source_url": "https://example.com/lg"},
    )
    hits = store.hybrid_search("LangGraph framework", top_k=5)
    ids = {h.get("doc_id") for h in hits}
    recall = 1.0 if doc_id in ids else 0.0
    record("rag_recall_proxy", passed=recall)
    assert recall >= 1.0
