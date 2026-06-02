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


def test_knowledge_store_domain_filter(isolated_stores):
    store = get_knowledge_store()
    code_id = store.upsert_document(
        "Code Guide",
        "Prefer small diffs and run tests.",
        {"domain": "code", "topic": "code"},
    )
    writing_id = store.upsert_document(
        "Writing Guide",
        "Use chapter pacing and foreshadowing.",
        {"domain": "writing", "topic": "writing"},
    )
    common_id = store.upsert_document(
        "Common Guide",
        "Always explain decisions clearly.",
        {"domain": "common", "topic": "general"},
    )

    code_hits = store.hybrid_search("guide", domains={"code", "common"})
    code_ids = {h["doc_id"] for h in code_hits}
    assert code_id in code_ids
    assert common_id in code_ids
    assert writing_id not in code_ids
