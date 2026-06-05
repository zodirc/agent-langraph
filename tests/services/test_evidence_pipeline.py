"""Unit tests for evidence operating system pipeline (§9.2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.runtime.evidence_models import FailureTag, RetrievalDecision
from app.services.evidence_arbitration import detect_conflicts
from app.services.evidence_assembly import (
    apply_admission_gate,
    assemble_evidence_packets,
    deduplicate_candidates,
    hits_to_candidates,
)
from app.services.grounding_check import check_grounding
from app.services.query_builder import build_query_object
from app.services.retrieval_decision import build_retrieval_decision
from app.services.retrieval_observability import build_retrieval_trace, infer_recall_failures


def _state(**kwargs):
    base = {
        "task_id": "t1",
        "session_id": "s1",
        "user_id": "u1",
        "task_type": "qa",
        "input_payload": {"goal": "How to fix ConnectionError in Python API client?"},
        "skip_retrieval": False,
    }
    base.update(kwargs)
    return base


def test_retrieval_decision_classifies_code_fix(monkeypatch):
    monkeypatch.setattr("app.services.retrieval_decision.settings.SKIP_RETRIEVAL_WHEN_NO_TOOLS", False)
    decision = build_retrieval_decision(_state())
    assert decision.need_retrieval is True
    assert decision.purpose == "code_fix"
    assert decision.answer_mode in ("refuse_if_insufficient", "strict_grounded", "best_effort_grounded")


def test_retrieval_decision_skip_when_planning_skips():
    decision = build_retrieval_decision(_state(skip_retrieval=True))
    assert decision.need_retrieval is False
    assert decision.skip_reason == "planning_skip_retrieval"


def test_query_object_extracts_must_have_terms():
    state = _state(
        input_payload={"goal": "ERROR_CONNECTION_REFUSED in config.yaml timeout setting"}
    )
    decision = build_retrieval_decision(state)
    q = build_query_object(state, decision)
    assert q.original_query
    assert q.standalone_query
    assert any("ERROR" in t or "config" in t.lower() for t in q.must_have_terms)


def test_admission_gate_soft_fallback(monkeypatch):
    monkeypatch.setattr("app.services.evidence_assembly.settings.RAG_RERANK_MIN_SCORE", 0.9)
    monkeypatch.setattr("app.services.evidence_assembly.settings.RETRIEVAL_ENABLE_ADMISSION_GATE", True)
    monkeypatch.setattr("app.services.evidence_assembly.settings.RETRIEVAL_ADMISSION_GATE_MODE", "soft")
    decision = RetrievalDecision(purpose="fact_qa")
    from app.runtime.evidence_models import QueryObject

    query = QueryObject(standalone_query="Python sort")
    hits = [{"doc_id": "a", "content": "Python sort uses Timsort.", "rerank_score": 0.2}]
    candidates = hits_to_candidates(hits)
    admitted, rejected, reasons = apply_admission_gate(candidates, query, decision)
    # Soft mode: low-score items may still pass (unlike hard mode which rejects all).
    assert len(admitted) >= 1
    assert reasons.get("shadow_would_reject", 0) >= 1 or reasons.get("below_threshold", 0) >= 1


def test_deduplicate_same_source():
    from app.runtime.evidence_models import CandidateEvidence, ScoreBreakdown

    c1 = CandidateEvidence(
        source_id="doc1", chunk_id="c1", content="Python sort algorithm Timsort details here",
        score_breakdown=ScoreBreakdown(total=0.9),
    )
    c2 = CandidateEvidence(
        source_id="doc1", chunk_id="c2", content="Python sort algorithm Timsort similar text",
        score_breakdown=ScoreBreakdown(total=0.8),
    )
    result = deduplicate_candidates([c1, c2], max_per_source=1)
    assert len(result) == 1


def test_assemble_evidence_packets_snippet(monkeypatch):
    monkeypatch.setattr("app.services.evidence_assembly.settings.RAG_SNIPPET_ENABLED", True)
    monkeypatch.setattr("app.services.evidence_assembly.settings.RETRIEVAL_ENABLE_SNIPPET_FIRST", True)
    from app.runtime.evidence_models import CandidateEvidence, QueryObject, ScoreBreakdown

    decision = RetrievalDecision(purpose="fact_qa")
    query = QueryObject(standalone_query="Python sort")
    cand = CandidateEvidence(
        chunk_id="x",
        source_id="p",
        title="Algo",
        content="无关背景。Python 使用 Timsort 排序。更多无关。",
        score_breakdown=ScoreBreakdown(total=0.8),
    )
    packets = assemble_evidence_packets([cand], query, decision)
    assert len(packets) == 1
    assert "Python" in packets[0].snippet_text or "Timsort" in packets[0].snippet_text


def test_grounding_detects_fake_citation():
    hits = [{"doc_id": "real_doc", "content": "Python uses Timsort for sorting."}]
    answer = "Python sorting uses Timsort. [fake_doc_id_not_injected]"
    result = check_grounding(answer, hits=hits, answer_mode="strict_grounded")
    assert "fake_doc_id_not_injected" in result.fake_citations
    assert "fake_citation" in result.failure_tags


def test_infer_recall_failures_no_recall():
    from app.runtime.evidence_models import QueryObject

    tags = infer_recall_failures(
        need_retrieval=True,
        raw_count=0,
        admitted_count=0,
        query=QueryObject(original_query="it"),
    )
    assert "no_recall" in tags


def test_build_retrieval_trace():
    from app.runtime.evidence_models import QueryObject

    trace = build_retrieval_trace(
        query=QueryObject(original_query="q", standalone_query="q expanded"),
        decision=RetrievalDecision(purpose="fact_qa"),
        candidates=[],
        admitted=[],
        packets=[],
        filter_reasons={},
        failure_tags=["no_recall"],
    )
    assert trace.purpose == "fact_qa"
    assert trace.failure_tags == ["no_recall"]


_FAILURE_CASES = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "eval"
        / "data"
        / "evidence_failure_cases.json"
    ).read_text(encoding="utf-8")
)


@pytest.mark.parametrize("fc", _FAILURE_CASES, ids=[f["tag"] for f in _FAILURE_CASES])
def test_failure_taxonomy_cases_documented(fc):
    """Each failure tag has a minimal fixture in the case library (§9.2 / §9.6)."""
    assert fc["tag"] in {t.value for t in FailureTag} or fc["tag"] in {
        "gate_all_filtered",
        "admission_failure",
    }


def test_query_object_history_constraints():
    state = _state(
        session_turn=3,
        conversation_history=[
            {"role": "user", "content": "分析 MyService API"},
            {"role": "assistant", "content": "好的"},
            {"role": "user", "content": "它的超时配置呢"},
        ],
        input_payload={"goal": "它的超时配置呢"},
    )
    decision = build_retrieval_decision(state)
    q = build_query_object(state, decision)
    assert q.original_query
    assert q.standalone_query != q.original_query or q.must_have_terms


def test_admission_stale_evidence_hard_mode(monkeypatch):
    monkeypatch.setattr("app.services.evidence_assembly.settings.RETRIEVAL_ADMISSION_GATE_MODE", "hard")
    monkeypatch.setattr("app.services.evidence_assembly.settings.RAG_RERANK_MIN_SCORE", 0.1)
    from app.runtime.evidence_models import QueryObject

    decision = RetrievalDecision(purpose="fact_qa", freshness_required=True)
    query = QueryObject(standalone_query="latest", time_scope="latest")
    hits = [
        {
            "doc_id": "old",
            "content": "old version",
            "rerank_score": 0.9,
            "metadata": {"deprecated": True},
        }
    ]
    candidates = hits_to_candidates(hits)
    admitted, rejected, reasons = apply_admission_gate(candidates, query, decision)
    assert reasons.get("stale_evidence", 0) >= 1 or len(rejected) >= 1


def test_run_evidence_pipeline_integration(monkeypatch):
    from app.services.evidence_pipeline import run_evidence_pipeline

    monkeypatch.setattr(
        "app.services.retrieval_decision.settings.SKIP_RETRIEVAL_WHEN_NO_TOOLS", False
    )
    state = _state()
    raw = [
        {
            "doc_id": "d1",
            "title": "Fix",
            "content": "ERROR_CONNECTION_REFUSED: check port 8080.",
            "rerank_score": 0.85,
            "relevance_passed": True,
        }
    ]
    patch = run_evidence_pipeline(state, raw)
    assert patch["retrieval_decision"]["purpose"] == "code_fix"
    assert patch["evidence_packets"]
    assert patch["retrieval_trace"]["admitted_count"] >= 1


def test_reasoning_grounding_instructions():
    from app.services.reasoning_grounding import build_grounding_instructions

    state = _state(
        retrieval_decision={"answer_mode": "strict_grounded"},
        evidence_packets=[{"packet_id": "p1", "snippet_text": "x"}],
    )
    text = build_grounding_instructions(state)
    assert "ONLY" in text or "evidence" in text.lower()


def test_conflict_detection_different_values():
    from app.runtime.evidence_models import CandidateEvidence

    a = CandidateEvidence(
        source_id="s1",
        source_type="knowledge",
        authority_level=0.8,
        content="timeout: 30\nport: 8080",
    )
    b = CandidateEvidence(
        source_id="s2",
        source_type="discussion",
        authority_level=0.4,
        content="timeout: 60\nport: 8080",
    )
    conflicts = detect_conflicts([a, b])
    assert any(c.field_key == "timeout" for c in conflicts)
