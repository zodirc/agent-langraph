"""Conflict arbitration tests (§9.2 / §4.4)."""

from __future__ import annotations

import pytest

from app.runtime.evidence_models import CandidateEvidence
from app.services.evidence_arbitration import (
    arbitrate_between,
    authority_prior,
    detect_conflicts,
    source_priority,
)


def test_source_priority_order():
    assert source_priority("user_input") > source_priority("tool_result")
    assert source_priority("tool_result") > source_priority("knowledge")
    assert source_priority("knowledge") > source_priority("discussion")


@pytest.mark.parametrize(
    "a_type,b_type,expected_winner",
    [
        ("tool_result", "knowledge", "a"),
        ("official_docs", "discussion", "a"),
        ("discussion", "official_docs", "b"),
    ],
)
def test_arbitrate_between_priority(a_type, b_type, expected_winner):
    winner, reason = arbitrate_between(a_type, b_type)
    assert winner == expected_winner
    assert reason


def test_arbitrate_unresolved_same_tier():
    winner, reason = arbitrate_between("knowledge", "chroma")
    assert reason == "unresolved"


def test_detect_conflicts_prefers_high_authority():
    a = CandidateEvidence(
        source_id="official",
        source_type="official_docs",
        authority_level=0.85,
        content="timeout: 30",
    )
    b = CandidateEvidence(
        source_id="forum",
        source_type="discussion",
        authority_level=0.35,
        content="timeout: 60",
    )
    conflicts = detect_conflicts([a, b])
    assert conflicts
    assert conflicts[0].priority_winner == "official"
    assert conflicts[0].arbitration_reason.startswith("higher_authority")


def test_authority_prior_configurable(monkeypatch):
    monkeypatch.setattr(
        "app.services.evidence_arbitration.settings.RETRIEVAL_SOURCE_AUTHORITY_PRIOR",
        {"custom": 0.99},
    )
    assert authority_prior("custom") == 0.99
