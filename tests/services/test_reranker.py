from app.services.reranker import rerank


def test_rerank_orders_by_relevance(monkeypatch):
    monkeypatch.setattr("app.services.reranker.settings.RAG_RERANK_ENABLED", True)
    docs = [
        {"doc_id": "a", "content": "unrelated topic", "score": 0.9},
        {"doc_id": "b", "content": "Python sorting algorithms explained", "score": 0.1},
    ]
    result = rerank("Python sort", docs, top_k=2)
    assert result[0]["doc_id"] == "b"


def test_rerank_disabled_returns_truncated(monkeypatch):
    monkeypatch.setattr("app.services.reranker.settings.RAG_RERANK_ENABLED", False)
    docs = [{"doc_id": "1"}, {"doc_id": "2"}, {"doc_id": "3"}]
    assert len(rerank("q", docs, top_k=2)) == 2


def test_rerank_lexical_backend_default(monkeypatch):
    monkeypatch.setattr("app.services.reranker.settings.RAG_RERANK_ENABLED", True)
    monkeypatch.setattr("app.services.reranker.settings.RAG_RERANK_BACKEND", "lexical")
    docs = [
        {"doc_id": "a", "content": "irrelevant"},
        {"doc_id": "b", "content": "Python machine learning"},
    ]
    result = rerank("Python ML", docs, top_k=1)
    assert result[0]["doc_id"] == "b"
