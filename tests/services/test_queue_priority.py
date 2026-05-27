"""Queue priority alignment tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.queue_priority import (
    celery_task_priority,
    effective_priority,
    effective_priority_from_row,
)


def test_effective_priority_starvation_boost() -> None:
    old = (datetime.now(timezone.utc) - timedelta(seconds=900)).isoformat()
    score = effective_priority(base_priority=0, enqueued_at=old)
    assert score >= 2


def test_effective_priority_from_row() -> None:
    row = {"priority": 1, "enqueued_at": datetime.now(timezone.utc).isoformat()}
    assert effective_priority_from_row(row) == 1


def test_celery_task_priority_capped() -> None:
    assert celery_task_priority(100) == 9
    assert celery_task_priority(3) == 3
