from app.services.rag_metrics import (
    citation_support_rate,
    diversity_at_context,
    evaluate_evidence_pipeline,
    evidence_precision_at_budget,
    grounded_answer_rate,
    answer_grounding_score,
    context_precision,
    evaluate_retrieval,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)


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
    assert m["precision@2"] == 0.5
    assert m["mrr"] == 1.0


def test_precision_at_k():
    assert precision_at_k(["a", "b", "c"], ["a", "d"], k=3) == 1 / 3


def test_context_precision():
    hits = [
        {"doc_id": "a", "relevance_passed": True},
        {"doc_id": "b", "relevance_passed": True},
    ]
    assert context_precision(hits, ["a"]) == 0.5


def test_answer_grounding_score():
    assert answer_grounding_score(["a", "b"], ["a", "c"]) == 0.5


def test_evidence_precision_at_budget():
    hits = [{"doc_id": "a", "relevance_passed": True}]
    assert evidence_precision_at_budget(hits, ["a"]) == 1.0


def test_diversity_at_context():
    hits = [
        {"doc_id": "c1", "metadata": {"parent_doc_id": "p1"}},
        {"doc_id": "c2", "metadata": {"parent_doc_id": "p2"}},
    ]
    assert diversity_at_context(hits) == 1.0


def test_citation_support_rate():
    assert citation_support_rate(["a", "b"], ["a"]) == 0.5


def test_grounded_answer_rate():
    assert grounded_answer_rate([{"grounded": True}, {"faithful": False}]) == 0.5


def test_evaluate_evidence_pipeline():
    trace = {"candidate_count": 5, "admitted_count": 2, "injected_count": 2, "failure_tags": []}
    hits = [{"doc_id": "a", "relevance_passed": True}]
    m = evaluate_evidence_pipeline(trace, relevant_ids=["a"], injected_hits=hits)
    assert m["evidence_precision_at_budget"] == 1.0
    assert m["candidate_count"] == 5.0
