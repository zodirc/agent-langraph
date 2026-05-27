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


def evaluate_retrieval(
    hits: list[dict[str, Any]],
    relevant_ids: list[str],
    *,
    k: int = 5,
) -> dict[str, float]:
    retrieved = [str(h.get("doc_id")) for h in hits if h.get("doc_id")]
    return {
        f"recall@{k}": recall_at_k(retrieved, relevant_ids, k=k),
        "mrr": mrr(retrieved, relevant_ids),
        f"ndcg@{k}": ndcg_at_k(retrieved, relevant_ids, k=k),
    }
