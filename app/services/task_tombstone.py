"""In-process tombstones for deleted tasks — drop late state writes."""

from __future__ import annotations

import threading

_lock = threading.Lock()
_tombstones: set[str] = set()


def mark_task_tombstone(task_id: str) -> None:
    with _lock:
        _tombstones.add(str(task_id))


def is_task_tombstoned(task_id: str) -> bool:
    with _lock:
        return str(task_id) in _tombstones


def clear_task_tombstone(task_id: str) -> None:
    with _lock:
        _tombstones.discard(str(task_id))


def clear_all_tombstones_for_tests() -> None:
    with _lock:
        _tombstones.clear()
