"""RAG golden suite: Recall@k, MRR, rerank, faithfulness, citations (8+ cases)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.services.rag_eval import check_faithfulness, merge_citations
from app.services.rag_metrics import evaluate_retrieval, mrr, recall_at_k
from app.services.reranker import rerank
from tests.eval.eval_metrics import record

_TASKS_PATH = Path(__file__).parent / "rag_golden_tasks.yaml"


def _load_tasks() -> list[dict]:
    data = yaml.safe_load(_TASKS_PATH.read_text(encoding="utf-8"))
    return list(data.get("tasks") or [])


@pytest.mark.parametrize("task", _load_tasks(), ids=lambda t: t["id"])
def test_rag_golden_task(
    task: dict, isolated_stores, test_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_id = task["id"]
    from app.services.knowledge_store import get_knowledge_store

    store = get_knowledge_store()

    if task_id in ("rag_faithful_answer", "rag_unfaithful_answer"):
        docs = [{"doc_id": "d1", "content": task["doc_content"]}]
        result = check_faithfulness(task["answer"], docs)
        expect = bool(task["expect_faithful"])
        assert result["faithful"] is expect
        record(task_id, passed=1.0 if result["faithful"] == expect else 0.0)
        record(f"{task_id}_faithfulness", faithfulness=float(result["score"]))
        return

    if task_id == "rag_citation_parse":
        docs = [
            {
                "doc_id": task["doc_id"],
                "title": "T",
                "metadata": {},
            }
        ]
        cites = merge_citations(task["answer"], docs)
        ok = any(c.get("doc_id") == task["expect_citation_id"] for c in cites)
        assert ok
        record(task_id, passed=1.0)
        return

    if task_id == "rag_rerank_order":
        monkeypatch.setattr("app.services.reranker.settings.RAG_RERANK_ENABLED", True)
        docs = [
            {"doc_id": "decoy", "title": task["decoy_title"], "content": task["decoy_content"], "score": 0.99},
            {"doc_id": "good", "title": task["title"], "content": task["content"], "score": 0.1},
        ]
        ranked = rerank(task["query"], docs, top_k=2)
        top_text = (ranked[0].get("title", "") + ranked[0].get("content", "")).lower()
        assert task["expect_top_doc_contains"] in top_text
        record(task_id, passed=1.0)
        return

    # Retrieval-based cases
    doc_id = store.upsert_document(
        task.get("title", "Doc"),
        task.get("content", ""),
        metadata={"source_url": "https://example.com/doc"},
    )
    relevant = list(task.get("relevant_doc_ids") or [])
    if task_id != "rag_empty_relevant":
        relevant.append(doc_id)

    if task.get("decoy_title"):
        store.upsert_document(
            task["decoy_title"],
            task["decoy_content"],
            metadata={},
        )

    hits = store.hybrid_search(task["query"], top_k=5)
    metrics = evaluate_retrieval(hits, relevant, k=5)

    if "expect_recall_min" in task:
        recall = metrics.get("recall@5", 0.0)
        assert recall >= float(task["expect_recall_min"])
        record(task_id, passed=recall)
        record(f"{task_id}_recall@5", score=recall)

    if "expect_mrr_min" in task:
        retrieved = [str(h.get("doc_id")) for h in hits]
        mrr_val = mrr(retrieved, relevant)
        assert mrr_val >= float(task["expect_mrr_min"])
        record(task_id, passed=mrr_val)
        record(f"{task_id}_mrr", score=mrr_val)
