"""RAG retrieval metrics: Recall@k, MRR, NDCG."""

from __future__ import annotations

import math
from typing import Any


def recall_at_k(
    retrieved_ids: list[str],
    relevant_ids: list[str],
    *,
    k: int = 5,
) -> float:
    if not relevant_ids:
        return 1.0 if not retrieved_ids else 0.0
    top = retrieved_ids[:k]
    hits = sum(1 for rid in relevant_ids if rid in top)
    return hits / len(relevant_ids)


def mrr(retrieved_ids: list[str], relevant_ids: list[str]) -> float:
    if not relevant_ids:
        return 0.0
    relevant = set(relevant_ids)
    for rank, doc_id in enumerate(retrieved_ids, start=1):
        if doc_id in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(
    retrieved_ids: list[str],
    relevant_ids: list[str],
    *,
    k: int = 5,
) -> float:
    if not relevant_ids:
        return 0.0
    relevant = set(relevant_ids)
    dcg = 0.0
    for rank, doc_id in enumerate(retrieved_ids[:k], start=1):
        rel = 1.0 if doc_id in relevant else 0.0
        dcg += rel / math.log2(rank + 1)
    ideal_hits = min(len(relevant_ids), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    if idcg <= 0:
        return 0.0
    return dcg / idcg


def precision_at_k(
    retrieved_ids: list[str],
    relevant_ids: list[str],
    *,
    k: int = 5,
) -> float:
    if not retrieved_ids:
        return 0.0
    top = retrieved_ids[:k]
    if not top:
        return 0.0
    relevant = set(relevant_ids)
    hits = sum(1 for doc_id in top if doc_id in relevant)
    return hits / len(top)


def context_precision(
    injected_hits: list[dict[str, Any]],
    relevant_ids: list[str],
) -> float:
    """Fraction of prompt-injected chunks that are gold-relevant."""
    if not injected_hits:
        return 0.0 if relevant_ids else 1.0
    relevant = set(relevant_ids)
    passed = [
        h for h in injected_hits if h.get("relevance_passed", True)
    ]
    if not passed:
        return 0.0
    hits = sum(
        1
        for h in passed
        if str(h.get("doc_id")) in relevant
        or str((h.get("metadata") or {}).get("parent_doc_id")) in relevant
    )
    return hits / len(passed)


def answer_grounding_score(
    cited_ids: list[str],
    relevant_ids: list[str],
    *,
    high_score_ids: list[str] | None = None,
) -> float:
    """Fraction of cited chunks that are gold-relevant (optionally high-score)."""
    if not cited_ids:
        return 0.0
    relevant = set(relevant_ids)
    pool = set(high_score_ids) if high_score_ids else relevant
    hits = sum(1 for doc_id in cited_ids if doc_id in pool)
    return hits / len(cited_ids)


def evaluate_retrieval(
    hits: list[dict[str, Any]],
    relevant_ids: list[str],
    *,
    k: int = 5,
) -> dict[str, float]:
    retrieved = [str(h.get("doc_id")) for h in hits if h.get("doc_id")]
    return {
        f"recall@{k}": recall_at_k(retrieved, relevant_ids, k=k),
        f"precision@{k}": precision_at_k(retrieved, relevant_ids, k=k),
        "mrr": mrr(retrieved, relevant_ids),
        f"ndcg@{k}": ndcg_at_k(retrieved, relevant_ids, k=k),
    }


def evidence_precision_at_budget(
    injected_hits: list[dict[str, Any]],
    relevant_ids: list[str],
) -> float:
    """Evidence Precision@Budget — fraction of injected evidence that is relevant."""
    return context_precision(injected_hits, relevant_ids)


def coverage_at_intent(
    injected_hits: list[dict[str, Any]],
    intent_facets: list[str],
) -> float:
    """Coverage@Intent — how many intent facets appear in injected evidence."""
    if not intent_facets:
        return 1.0
    corpus = " ".join(
        f"{h.get('title', '')} {h.get('content', '')}" for h in injected_hits
    ).lower()
    covered = sum(1 for facet in intent_facets if facet.lower() in corpus)
    return covered / len(intent_facets)


def diversity_at_context(injected_hits: list[dict[str, Any]]) -> float:
    """Diversity@Context — unique source ratio in injected context."""
    if not injected_hits:
        return 0.0
    sources: set[str] = set()
    for h in injected_hits:
        meta = h.get("metadata") if isinstance(h.get("metadata"), dict) else {}
        sid = str(meta.get("parent_doc_id") or h.get("doc_id") or "")
        if sid:
            sources.add(sid)
    return len(sources) / len(injected_hits)


def citation_support_rate(
    cited_ids: list[str],
    supported_ids: list[str],
) -> float:
    if not cited_ids:
        return 0.0
    supported = set(supported_ids)
    hits = sum(1 for cid in cited_ids if cid in supported)
    return hits / len(cited_ids)


def grounded_answer_rate(grounding_results: list[dict[str, Any]]) -> float:
    if not grounding_results:
        return 0.0
    grounded = sum(1 for g in grounding_results if g.get("grounded") or g.get("faithful"))
    return grounded / len(grounding_results)


def freshness_accuracy(
    injected_hits: list[dict[str, Any]],
    *,
    require_latest: bool = True,
) -> float:
    """Fraction of injected hits that are non-stale when latest is required."""
    if not require_latest or not injected_hits:
        return 1.0 if not require_latest else 0.0
    fresh = 0
    for h in injected_hits:
        meta = h.get("metadata") if isinstance(h.get("metadata"), dict) else {}
        if not (meta.get("deprecated") or meta.get("superseded") or meta.get("stale")):
            fresh += 1
    return fresh / len(injected_hits)


def authority_accuracy(
    injected_hits: list[dict[str, Any]],
    *,
    high_authority_types: set[str] | None = None,
) -> float:
    """Fraction of top injected hits from high-authority source types."""
    if not injected_hits:
        return 0.0
    high = high_authority_types or {"official_docs", "user_input", "tool_result", "knowledge"}
    hits = sum(
        1
        for h in injected_hits
        if str(h.get("source") or (h.get("metadata") or {}).get("source_type") or "knowledge") in high
        or float(h.get("authority_level") or 0) >= 0.7
    )
    return hits / len(injected_hits)


def evaluate_evidence_pipeline(
    trace: dict[str, Any],
    *,
    relevant_ids: list[str] | None = None,
    injected_hits: list[dict[str, Any]] | None = None,
) -> dict[str, float]:
    """Extended offline metrics aligned with evidence OS observability."""
    metrics: dict[str, float] = {
        "candidate_count": float(trace.get("candidate_count") or 0),
        "admitted_count": float(trace.get("admitted_count") or 0),
        "injected_count": float(trace.get("injected_count") or 0),
    }
    if relevant_ids and injected_hits is not None:
        metrics["evidence_precision_at_budget"] = evidence_precision_at_budget(
            injected_hits, relevant_ids
        )
        metrics["diversity_at_context"] = diversity_at_context(injected_hits)
        metrics["freshness_accuracy"] = freshness_accuracy(injected_hits)
        metrics["authority_accuracy"] = authority_accuracy(injected_hits)
    failure_tags = trace.get("failure_tags") or []
    metrics["failure_tag_count"] = float(len(failure_tags))
    return metrics
