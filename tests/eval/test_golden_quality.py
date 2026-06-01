"""Validate golden baseline files have required fields."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_EVAL_DIR = Path(__file__).parent


@pytest.mark.parametrize(
    "filename",
    ["baseline.json", "integration_baseline.json"],
)
def test_baseline_files_have_tasks(filename: str) -> None:
    path = _EVAL_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} missing")
    data = json.loads(path.read_text(encoding="utf-8"))
    tasks = data.get("tasks")
    assert isinstance(tasks, (dict, list))
    if isinstance(tasks, dict):
        assert len(tasks) >= 1
    else:
        assert len(tasks) >= 1
        for sample in tasks:
            assert "id" in sample


def test_integration_samples_have_input() -> None:
    # integration_baseline.json stores aggregated pass rates (dict keyed by id).
    # The executable integration samples live in integration_cases.json.
    path = _EVAL_DIR / "integration_cases.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for sample in data.get("cases") or []:
        assert "input" in sample
        assert "expected_status" in sample
