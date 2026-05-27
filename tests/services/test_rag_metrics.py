from app.services.rag_metrics import evaluate_retrieval, mrr, ndcg_at_k, recall_at_k


def test_recall_at_k():
    assert recall_at_k(["a", "b", "c"], ["a", "d"], k=3) == 0.5


def test_mrr_first_position():
    assert mrr(["x", "target", "y"], ["target"]) == 0.5


def test_ndcg_perfect():
    score = ndcg_at_k(["a", "b"], ["a", "b"], k=2)
    assert score == 1.0


def test_evaluate_retrieval_bundle():
    hits = [{"doc_id": "1"}, {"doc_id": "2"}]
    m = evaluate_retrieval(hits, ["1"], k=2)
    assert m["recall@2"] == 1.0
    assert m["mrr"] == 1.0
