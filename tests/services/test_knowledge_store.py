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


def test_keyword_search_supports_chinese_tokens(isolated_stores):
    store = get_knowledge_store()
    doc_id = store.upsert_document(
        "写作规范",
        "建议保持自然口吻，避免机械重复，注意段落衔接。",
        {"domain": "writing", "topic": "writing"},
    )
    hits = store.keyword_search("自然口吻段落衔接", top_k=5, domains={"writing"})
    assert any(h["doc_id"] == doc_id for h in hits)


def test_hybrid_search_limits_duplicate_parent_doc(isolated_stores):
    store = get_knowledge_store()
    parent = "doc-parent-1"
    store.upsert_document(
        "章节1",
        "第一段内容，包含主题A。",
        {"domain": "common", "parent_doc_id": parent, "chunk_index": 0},
        doc_id="chunk-1",
    )
    store.upsert_document(
        "章节1-续",
        "第二段内容，包含主题A与补充信息。",
        {"domain": "common", "parent_doc_id": parent, "chunk_index": 1},
        doc_id="chunk-2",
    )
    store.upsert_document(
        "独立文档",
        "完全不同的主题B。",
        {"domain": "common", "parent_doc_id": "doc-parent-2", "chunk_index": 0},
        doc_id="chunk-3",
    )
    hits = store.hybrid_search("主题A", top_k=5, domains={"common"})
    parent_hits = [h for h in hits if (h.get("metadata") or {}).get("parent_doc_id") == parent]
    assert len(parent_hits) <= 2
