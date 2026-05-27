"""Collect per-golden-task metrics for baseline regression (Ch19 CI)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_METRICS: dict[str, dict[str, float]] = {}


def record(task_id: str, **metrics: float) -> None:
    entry = _METRICS.setdefault(task_id, {})
    for key, value in metrics.items():
        entry[key] = float(value)


def snapshot() -> dict[str, dict[str, float]]:
    return {k: dict(v) for k, v in _METRICS.items()}


def clear() -> None:
    _METRICS.clear()


def load_baseline(path: Path) -> dict[str, dict[str, float]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    tasks = data.get("tasks") if isinstance(data, dict) else data
    if not isinstance(tasks, dict):
        return {}
    return {str(k): {mk: float(mv) for mk, mv in v.items()} for k, v in tasks.items()}


def save_baseline(path: Path, metrics: dict[str, dict[str, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "tasks": metrics}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def compare_baseline(
    current: dict[str, dict[str, float]],
    baseline: dict[str, dict[str, float]],
    *,
    max_regression: float,
) -> list[str]:
    """Return human-readable regression messages."""
    errors: list[str] = []
    for task_id, base_metrics in baseline.items():
        cur_metrics = current.get(task_id)
        if cur_metrics is None:
            # Suite-specific baselines (e.g. rag-only) may omit tasks not executed this run.
            continue
        for key, base_val in base_metrics.items():
            cur_val = cur_metrics.get(key)
            if cur_val is None:
                errors.append(f"{task_id}.{key}: missing metric")
                continue
            if base_val == 0:
                if cur_val < base_val * (1 - max_regression):
                    errors.append(
                        f"{task_id}.{key}: {cur_val} < baseline {base_val} (>{max_regression:.0%} drop)"
                    )
                continue
            drop = (base_val - cur_val) / abs(base_val)
            if drop > max_regression:
                errors.append(
                    f"{task_id}.{key}: {cur_val} vs baseline {base_val} (drop {drop:.1%} > {max_regression:.0%})"
                )
    return errors
