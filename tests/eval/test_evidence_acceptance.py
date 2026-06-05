"""§9.4 acceptance criteria smoke tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.rag_metrics import (
    authority_accuracy,
    citation_support_rate,
    diversity_at_context,
    freshness_accuracy,
    grounded_answer_rate,
)


def test_skip_retrieval_when_no_tools(monkeypatch):
    from app.services.retrieval_decision import build_retrieval_decision

    monkeypatch.setattr("app.services.retrieval_decision.settings.SKIP_RETRIEVAL_WHEN_NO_TOOLS", True)
    d = build_retrieval_decision(
        {"task_type": "qa", "skip_retrieval": False, "input_payload": {"goal": "hello"}, "selected_tools": []}
    )
    assert d.need_retrieval is False


def test_comparative_diversity_metric():
    hits = [
        {"doc_id": "a", "metadata": {"parent_doc_id": "p1"}},
        {"doc_id": "b", "metadata": {"parent_doc_id": "p2"}},
    ]
    assert diversity_at_context(hits) == 1.0


def test_freshness_and_authority_metrics():
    hits = [{"doc_id": "a", "metadata": {}, "authority_level": 0.85, "source": "official_docs"}]
    assert freshness_accuracy(hits) == 1.0
    assert authority_accuracy(hits) == 1.0


def test_citation_support_from_labels():
    labels = json.loads(
        (Path(__file__).parent / "data" / "evidence_citation_labels.json").read_text()
    )
    supported = [l["citation"] for l in labels if l.get("supports")]
    cited = [l["citation"] for l in labels]
    assert citation_support_rate(cited, supported) >= 0.5


def test_grounded_answer_rate_metric():
    assert grounded_answer_rate([{"grounded": True}, {"grounded": False}]) == 0.5
