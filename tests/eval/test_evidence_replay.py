"""Replay tests for evidence pipeline (§9.3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.eval.run_evidence_replay import replay_case

_CASES = json.loads(
    (Path(__file__).parent / "data" / "evidence_replay_cases.json").read_text(encoding="utf-8")
)
_PURPOSE_LABELS = json.loads(
    (Path(__file__).parent / "data" / "evidence_purpose_labels.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", _CASES, ids=[c["id"] for c in _CASES])
def test_replay_case_runs(case, monkeypatch):
    monkeypatch.setattr(
        "app.services.retrieval_decision.settings.SKIP_RETRIEVAL_WHEN_NO_TOOLS", False
    )
    result = replay_case(case)
    assert result["id"] == case["id"]
    assert result["admitted"] is not None
    assert isinstance(result["failure_tags"], list)


def test_replay_error_code_recall(monkeypatch):
    monkeypatch.setattr(
        "app.services.retrieval_decision.settings.SKIP_RETRIEVAL_WHEN_NO_TOOLS", False
    )
    case = next(c for c in _CASES if c["id"] == "error_code")
    result = replay_case(case)
    assert result["recall_proxy"] >= 0.5


def test_replay_compare_diversity(monkeypatch):
    monkeypatch.setattr(
        "app.services.retrieval_decision.settings.SKIP_RETRIEVAL_WHEN_NO_TOOLS", False
    )
    case = next(c for c in _CASES if c["id"] == "compare")
    result = replay_case(case)
    assert result["injected"] >= 1


@pytest.mark.parametrize("label", _PURPOSE_LABELS, ids=[l["query"][:30] for l in _PURPOSE_LABELS])
def test_purpose_label_classification(label, monkeypatch):
    from app.services.retrieval_decision import build_retrieval_decision

    monkeypatch.setattr(
        "app.services.retrieval_decision.settings.SKIP_RETRIEVAL_WHEN_NO_TOOLS", False
    )
    state = {
        "task_id": "t",
        "session_id": "s",
        "task_type": "qa",
        "input_payload": {"goal": label["query"]},
        "skip_retrieval": False,
    }
    decision = build_retrieval_decision(state)
    expected = label["expected_purpose"]
    if expected == "general_grounded":
        assert decision.purpose in (
            "general_grounded",
            "planning_background",
            "fact_qa",
        )
    else:
        assert decision.purpose == expected
