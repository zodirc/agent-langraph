"""Shared priority + anti-starvation for SQLite and Celery queues (Ch20)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Union

from app.config.settings import settings

RowLike = Union[Mapping[str, Any], Any]


def effective_priority(
    *,
    base_priority: int,
    enqueued_at: str,
    now: Optional[datetime] = None,
) -> int:
    """
    Time-weighted priority boost (same formula as TaskQueueStore._effective_priority).
    Higher value = dequeued sooner.
    """
    base = int(base_priority)
    starvation_sec = int(getattr(settings, "QUEUE_STARVATION_SEC", 300))
    if starvation_sec <= 0:
        return base
    try:
        enqueued = datetime.fromisoformat(str(enqueued_at).replace("Z", "+00:00"))
        if enqueued.tzinfo is None:
            enqueued = enqueued.replace(tzinfo=timezone.utc)
        ref = now or datetime.now(timezone.utc)
        age = (ref - enqueued).total_seconds()
        boosts = int(age // starvation_sec)
        max_boost = int(getattr(settings, "QUEUE_STARVATION_MAX_BOOST", 3))
        return base + min(boosts, max_boost)
    except ValueError:
        return base


def effective_priority_from_row(row: RowLike) -> int:
    if hasattr(row, "keys"):
        base = int(row["priority"])
        enqueued = str(row["enqueued_at"])
    else:
        base = int(row["priority"])
        enqueued = str(row["enqueued_at"])
    return effective_priority(base_priority=base, enqueued_at=enqueued)


def celery_task_priority(effective: int) -> int:
    """Map effective priority to Celery broker priority 0–9 (higher = sooner)."""
    return max(0, min(9, effective))
