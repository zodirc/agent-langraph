from app.services.rag_domain_eval import context_redundancy_rate, evaluate_domain_tasks


def test_context_redundancy_rate_counts_irrelevant_hits():
    hits = [
        {"doc_id": "a", "title": "TXT 排版规范", "content": "段落与标点规范"},
        {"doc_id": "b", "title": "无关文档", "content": "数据库索引设计"},
    ]
    ratio = context_redundancy_rate("TXT 排版 怎么做", hits)
    assert ratio == 0.5


def test_evaluate_domain_tasks_returns_domain_buckets():
    tasks = [
        {"id": "w1", "domain": "writing", "query": "TXT 排版", "relevant_doc_ids": ["w-doc"]},
        {"id": "c1", "domain": "code", "query": "函数 重构", "relevant_doc_ids": ["c-doc"]},
        {"id": "x1", "domain": "unknown", "query": "通用规范", "relevant_doc_ids": ["g-doc"]},
    ]

    def _search(query: str, domain: str, top_k: int):
        _ = top_k
        if domain == "writing":
            return [{"doc_id": "w-doc", "title": "TXT 排版", "content": query}]
        if domain == "code":
            return [{"doc_id": "c-doc", "title": "函数 重构", "content": query}]
        return [{"doc_id": "g-doc", "title": "通用规范", "content": query}]

    result = evaluate_domain_tasks(tasks, top_k=3, search_fn=_search)
    assert result["summary"]["task_count"] == 3.0
    assert result["per_domain"]["writing"]["task_count"] == 1.0
    assert result["per_domain"]["code"]["task_count"] == 1.0
    # unknown domain should fall back to common
    assert result["per_domain"]["common"]["task_count"] == 1.0
    assert result["summary"]["recall@3"] == 1.0
