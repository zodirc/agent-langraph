"""Grounding / citation validation tests (§9.2)."""

from __future__ import annotations

import pytest

from app.runtime.evidence_models import EvidencePacket
from app.services.grounding_check import check_grounding


@pytest.mark.parametrize(
    "answer,hits,expected_grounded,expected_fake",
    [
        ("Python uses Timsort. [real_doc]", [{"doc_id": "real_doc", "content": "Timsort sort"}], True, []),
        ("Moon is cheese. [ghost]", [{"doc_id": "real_doc", "content": "Timsort"}], False, ["ghost"]),
        ("", [{"doc_id": "x", "content": "y"}], True, []),
        ("Therefore the answer is 42.", [], False, []),
    ],
)
def test_grounding_table(answer, hits, expected_grounded, expected_fake):
    result = check_grounding(answer, hits=hits, answer_mode="strict_grounded")
    if expected_fake:
        assert result.fake_citations == expected_fake
    if not hits and answer and "Therefore" in answer:
        assert result.grounded is False
        assert "unsupported_generation" in result.failure_tags


def test_grounding_with_evidence_packets():
    packets = [
        EvidencePacket(
            chunk_id="pkt1",
            source_id="src1",
            snippet_text="LangGraph supports checkpointing.",
        )
    ]
    answer = "LangGraph supports checkpointing for durable workflows."
    result = check_grounding(answer, packets=packets, answer_mode="best_effort_grounded")
    assert result.grounded is True
    assert result.score >= 0.5


def test_grounding_off_when_strictness_off(monkeypatch):
    monkeypatch.setattr("app.services.grounding_check.settings.RETRIEVAL_CITATION_CHECK_STRICTNESS", "off")
    result = check_grounding("anything", hits=[])
    assert result.grounded is True
