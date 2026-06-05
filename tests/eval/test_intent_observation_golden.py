"""Golden eval for intent observation routing (structural baseline)."""

from app.services.intent_observation_eval import (
    GOLDEN_CASES,
    MISSION_GOLDEN_CASES,
    MIXED_PROMPT_GOLDEN_CASES,
    run_structural_baseline,
)


def test_intent_observation_structural_golden_accuracy():
    report = run_structural_baseline()
    assert report["total"] >= len(GOLDEN_CASES)
    assert report["accuracy"] >= 0.5


def test_mission_golden_cases_defined():
    assert len(MISSION_GOLDEN_CASES) >= 3


def test_mixed_prompt_golden_cases_defined():
    assert len(MIXED_PROMPT_GOLDEN_CASES) >= 2
