"""Observability / failure taxonomy logging tests."""

from __future__ import annotations

from app.runtime.evidence_models import QueryObject, RetrievalDecision
from app.services.retrieval_observability import (
    build_retrieval_trace,
    infer_recall_failures,
    trace_to_audit,
)


def test_trace_to_audit_compact():
    trace = build_retrieval_trace(
        query=QueryObject(original_query="q", standalone_query="q2"),
        decision=RetrievalDecision(purpose="code_fix", answer_mode="refuse_if_insufficient"),
        candidates=[],
        admitted=[],
        packets=[],
        filter_reasons={"below_threshold": 2},
        failure_tags=["no_recall", "bad_query"],
    )
    audit = trace_to_audit(trace)
    assert audit["purpose"] == "code_fix"
    assert "no_recall" in audit["failure_tags"]
    assert audit["filtered_reasons"]["below_threshold"] == 2


def test_infer_recall_distinguishes_no_results_vs_gate():
    tags_empty = infer_recall_failures(
        need_retrieval=True, raw_count=0, admitted_count=0,
        query=QueryObject(original_query="x"),
    )
    assert "no_recall" in tags_empty

    tags_gated = infer_recall_failures(
        need_retrieval=True, raw_count=5, admitted_count=0,
        query=QueryObject(original_query="x"),
    )
    assert "gate_all_filtered" in tags_gated
