"""Retrieval search policy: lexical boost, freshness pre-filter, RRF weighting (§4.2.1)."""

from __future__ import annotations

import time
from typing import Any

from app.config.settings import settings
from app.runtime.evidence_models import QueryObject, RetrievalDecision


def should_use_lexical_heavy(query_obj: QueryObject | None, decision: RetrievalDecision | None) -> bool:
    if decision and decision.purpose == "code_fix":
        return True
    if not query_obj:
        return False
    if query_obj.must_have_terms:
        return True
    if "needs_code_fix" in (query_obj.task_constraints or []):
        return True
    return False


def lexical_rrf_weight(query_obj: QueryObject | None, decision: RetrievalDecision | None) -> float:
    """Boost lexical channel weight in RRF merge (default 1.0)."""
    if should_use_lexical_heavy(query_obj, decision):
        return float(getattr(settings, "RETRIEVAL_LEXICAL_RRF_WEIGHT", 1.8))
    return 1.0


def filter_stale_at_recall(
    hits: list[dict[str, Any]],
    *,
    query_obj: QueryObject | None = None,
    decision: RetrievalDecision | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Pre-filter deprecated/stale docs when latest scope required."""
    need_latest = (
        (decision and decision.freshness_required)
        or (query_obj and query_obj.time_scope == "latest")
    )
    if not need_latest:
        return hits, 0
    kept: list[dict[str, Any]] = []
    dropped = 0
    for hit in hits:
        meta = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
        if meta.get("deprecated") or meta.get("superseded") or meta.get("stale"):
            dropped += 1
            continue
        kept.append(hit)
    return kept, dropped


def recency_score(metadata: dict[str, Any]) -> float:
    """0-1 freshness prior from timestamp metadata."""
    if metadata.get("deprecated") or metadata.get("superseded"):
        return 0.1
    ts = metadata.get("updated_at") or metadata.get("timestamp") or metadata.get("created_at")
    if not ts:
        return 0.5
    try:
        from datetime import datetime, timezone

        if isinstance(ts, (int, float)):
            age_days = (time.time() - float(ts)) / 86400
        else:
            parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - parsed).total_seconds() / 86400
        if age_days <= 30:
            return 1.0
        if age_days <= 180:
            return 0.7
        if age_days <= 365:
            return 0.4
        return 0.2
    except Exception:
        return 0.5
