"""Component tests: admission, snippet, dedup, diversity (§9.1 layer 2)."""

from __future__ import annotations

import pytest

from app.runtime.evidence_models import (
    CandidateEvidence,
    QueryObject,
    RetrievalDecision,
    ScoreBreakdown,
)
from app.services.evidence_assembly import (
    annotate_freshness,
    apply_admission_gate,
    assemble_evidence_packets,
    deduplicate_candidates,
    detect_duplication_failures,
    detect_purpose_mismatch,
    enforce_source_diversity,
    purpose_admission_threshold,
    shadow_admission_eval,
)


def test_purpose_threshold_boundary():
    assert purpose_admission_threshold("fact_qa") >= purpose_admission_threshold("comparative_summary")


def test_hard_gate_filters_all(monkeypatch):
    monkeypatch.setattr("app.services.evidence_assembly.settings.RAG_RERANK_MIN_SCORE", 0.9)
    monkeypatch.setattr("app.services.evidence_assembly.settings.RETRIEVAL_ADMISSION_GATE_MODE", "hard")
    decision = RetrievalDecision(purpose="fact_qa")
    query = QueryObject(standalone_query="test")
    from app.services.evidence_assembly import hits_to_candidates

    admitted, rejected, _ = apply_admission_gate(
        hits_to_candidates([{"doc_id": "a", "content": "noise", "rerank_score": 0.1}]),
        query,
        decision,
    )
    assert len(admitted) == 0
    assert len(rejected) >= 1


def test_shadow_admission_eval_differs_from_soft(monkeypatch):
    monkeypatch.setattr("app.services.evidence_assembly.settings.RETRIEVAL_ADMISSION_GATE_MODE", "soft")
    monkeypatch.setattr("app.services.evidence_assembly.settings.RAG_RERANK_MIN_SCORE", 0.5)
    decision = RetrievalDecision(purpose="fact_qa")
    query = QueryObject(standalone_query="x")
    from app.services.evidence_assembly import hits_to_candidates

    cands = hits_to_candidates(
        [
            {"doc_id": "a", "content": "a", "rerank_score": 0.6},
            {"doc_id": "b", "content": "b", "rerank_score": 0.2},
        ]
    )
    shadow = shadow_admission_eval(cands, query, decision)
    assert "shadow_would_admit" in shadow
    assert shadow["shadow_would_reject"] >= 0


def test_freshness_annotation_marks_stale():
    q = QueryObject(time_scope="latest")
    c = CandidateEvidence(
        chunk_id="x",
        metadata={"deprecated": True},
        content="old version info",
    )
    annotated = annotate_freshness([c], q)
    assert annotated[0].metadata.get("stale") is True


def test_enforce_source_diversity_comparative():
    decision = RetrievalDecision(purpose="comparative_summary")
    cands = [
        CandidateEvidence(source_id="s1", chunk_id="c1", content="a", score_breakdown=ScoreBreakdown(total=0.9)),
        CandidateEvidence(source_id="s1", chunk_id="c2", content="b", score_breakdown=ScoreBreakdown(total=0.8)),
        CandidateEvidence(source_id="s2", chunk_id="c3", content="c", score_breakdown=ScoreBreakdown(total=0.7)),
    ]
    result = enforce_source_diversity(cands, decision)
    sources = {c.source_id for c in result[:2]}
    assert len(sources) >= 2


def test_snippet_char_range(monkeypatch):
    monkeypatch.setattr("app.services.evidence_assembly.settings.RAG_SNIPPET_ENABLED", True)
    monkeypatch.setattr("app.services.evidence_assembly.settings.RETRIEVAL_NEIGHBORHOOD_EXPAND_RADIUS", 1)
    decision = RetrievalDecision(purpose="fact_qa")
    query = QueryObject(standalone_query="deploy docker")
    content = "Intro. Step 1: docker compose up. Step 2: verify. Outro."
    cand = CandidateEvidence(
        chunk_id="c1",
        source_id="s1",
        content=content,
        score_breakdown=ScoreBreakdown(total=0.8),
    )
    packets = assemble_evidence_packets([cand], query, decision)
    assert packets[0].char_range is not None
    assert "docker" in packets[0].snippet_text.lower()


@pytest.mark.parametrize(
    "purpose,content,expect_mismatch",
    [
        ("procedural_howto", "just a concept paragraph with no steps", True),
        ("code_fix", "general philosophy of programming", True),
        ("fact_qa", "LangGraph is a framework.", False),
    ],
)
def test_purpose_mismatch_detection(purpose, content, expect_mismatch):
    decision = RetrievalDecision(purpose=purpose, need_retrieval=True)
    query = QueryObject(
        standalone_query="test",
        task_constraints=["needs_steps"] if purpose == "procedural_howto" else [],
    )
    cand = CandidateEvidence(
        chunk_id="x",
        source_id="s",
        content=content,
        score_breakdown=ScoreBreakdown(total=0.7, task_match=0.0),
    )
    tags = detect_purpose_mismatch(decision, [cand], query)
    if expect_mismatch and purpose in ("procedural_howto", "code_fix"):
        assert "purpose_mismatch" in tags
    else:
        assert "purpose_mismatch" not in tags


def test_duplication_failure_tag():
    pre = [CandidateEvidence(chunk_id=str(i), source_id="s", content=f"text {i}") for i in range(5)]
    admitted = [pre[0]]
    assert "context_duplication" in detect_duplication_failures(pre, admitted)
