from app.services.knowledge_store import get_knowledge_store


def test_knowledge_store_search(isolated_stores):
    store = get_knowledge_store()
    doc_id = store.upsert_document("Runtime", "LangGraph state graph execution", {})
    hits = store.hybrid_search("LangGraph state")
    assert len(hits) >= 1
    assert any(h["doc_id"] == doc_id for h in hits)


def test_knowledge_vector_and_keyword(isolated_stores):
    store = get_knowledge_store()
    store.upsert_document("Policy", "REVIEW HIGH CRITICAL policy engine rules", {})
    keyword = store.keyword_search("policy engine")
    assert len(keyword) >= 1
    vector = store.vector_search("policy engine")
    assert isinstance(vector, list)
