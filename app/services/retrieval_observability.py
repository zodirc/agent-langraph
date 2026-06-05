"""Retrieval observability: failure taxonomy and per-turn trace logging."""

from __future__ import annotations

import logging
from typing import Any

from app.config.settings import settings
from app.runtime.evidence_models import (
    CandidateEvidence,
    EvidenceConflict,
    EvidencePacket,
    FailureTag,
    QueryObject,
    RetrievalDecision,
    RetrievalTrace,
)

logger = logging.getLogger(__name__)


def build_retrieval_trace(
    *,
    query: QueryObject,
    decision: RetrievalDecision,
    candidates: list[CandidateEvidence],
    admitted: list[CandidateEvidence],
    packets: list[EvidencePacket],
    filter_reasons: dict[str, int],
    failure_tags: list[str],
    conflicts: list[EvidenceConflict] | None = None,
) -> RetrievalTrace:
    sample = [
        {
            "candidate_id": c.candidate_id,
            "total": c.score_breakdown.total,
            "filter_reason": c.filter_reason,
            "passed": c.relevance_passed,
        }
        for c in (admitted or candidates)[:5]
    ]
    return RetrievalTrace(
        original_query=query.original_query[:500],
        standalone_query=query.standalone_query[:500],
        purpose=decision.purpose,
        answer_mode=decision.answer_mode,
        candidate_count=len(candidates),
        admitted_count=len(admitted),
        injected_count=len(packets),
        filtered_reasons=filter_reasons,
        failure_tags=failure_tags,
        conflicts=conflicts or [],
        score_breakdown_sample=sample,
        debug_info={
            "must_have_terms": query.must_have_terms[:8],
            "task_constraints": query.task_constraints[:6],
            "skip_reason": decision.skip_reason,
        },
    )


def infer_recall_failures(
    *,
    need_retrieval: bool,
    raw_count: int,
    admitted_count: int,
    query: QueryObject,
) -> list[str]:
    tags: list[str] = []
    if need_retrieval and raw_count == 0:
        tags.append(FailureTag.NO_RECALL.value)
        if len(query.original_query.split()) <= 3 or not query.must_have_terms:
            tags.append(FailureTag.BAD_QUERY.value)
    if raw_count > 0 and admitted_count == 0:
        tags.append(FailureTag.GATE_ALL_FILTERED.value)
    return tags


def log_retrieval_trace(trace: RetrievalTrace) -> None:
    if not getattr(settings, "RETRIEVAL_LOG_FAILURE_TAXONOMY", True):
        return
    payload = trace.model_dump()
    # Trim for prod log volume.
    payload.pop("conflicts", None)
    logger.info(
        "retrieval_trace purpose=%s candidates=%d admitted=%d failures=%s",
        trace.purpose,
        trace.candidate_count,
        trace.admitted_count,
        trace.failure_tags,
        extra={"retrieval_trace": payload},
    )


def observe_retrieval_trace(trace: RetrievalTrace) -> None:
    """Push trace metrics to metrics service."""
    try:
        if not settings.METRICS_ENABLED:
            return
        from app.services.metrics_service import get_metrics_service

        svc = get_metrics_service()
        svc.observe_retrieval_trace(trace.model_dump())
    except Exception:
        pass


def trace_to_audit(trace: RetrievalTrace) -> dict[str, Any]:
    return {
        "purpose": trace.purpose,
        "answer_mode": trace.answer_mode,
        "candidate_count": trace.candidate_count,
        "admitted_count": trace.admitted_count,
        "injected_count": trace.injected_count,
        "failure_tags": trace.failure_tags,
        "filtered_reasons": trace.filtered_reasons,
        "standalone_query": trace.standalone_query[:200],
    }
