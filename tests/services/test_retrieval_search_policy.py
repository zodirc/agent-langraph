from app.runtime.evidence_models import QueryObject, RetrievalDecision
from app.services.retrieval_search_policy import (
    filter_stale_at_recall,
    lexical_rrf_weight,
    recency_score,
    should_use_lexical_heavy,
)


def test_lexical_heavy_for_code_fix():
    q = QueryObject(must_have_terms=["ERROR_X"])
    d = RetrievalDecision(purpose="code_fix")
    assert should_use_lexical_heavy(q, d) is True
    assert lexical_rrf_weight(q, d) >= 1.5


def test_filter_stale_at_recall():
    hits = [
        {"doc_id": "a", "metadata": {"deprecated": True}},
        {"doc_id": "b", "metadata": {}},
    ]
    kept, dropped = filter_stale_at_recall(
        hits,
        query_obj=QueryObject(time_scope="latest"),
        decision=RetrievalDecision(freshness_required=True),
    )
    assert dropped == 1
    assert len(kept) == 1


def test_recency_score_deprecated():
    assert recency_score({"deprecated": True}) <= 0.15
