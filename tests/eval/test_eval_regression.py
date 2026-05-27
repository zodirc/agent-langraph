"""Baseline regression helper tests."""

from __future__ import annotations

from pathlib import Path

from tests.eval.eval_metrics import compare_baseline, load_baseline, save_baseline, snapshot, record, clear


def test_compare_baseline_detects_regression(tmp_path: Path) -> None:
    clear()
    record("task_a", score=10.0)
    save_baseline(tmp_path / "baseline.json", snapshot())
    clear()
    record("task_a", score=8.0)
    errors = compare_baseline(snapshot(), load_baseline(tmp_path / "baseline.json"), max_regression=0.05)
    assert errors
    assert "task_a" in errors[0]


def test_compare_baseline_passes_stable(tmp_path: Path) -> None:
    clear()
    record("task_b", score=5.0)
    save_baseline(tmp_path / "b.json", snapshot())
    errors = compare_baseline(snapshot(), load_baseline(tmp_path / "b.json"), max_regression=0.05)
    assert not errors
