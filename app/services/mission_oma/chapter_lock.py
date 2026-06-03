"""Per-chapter artifact locks for OMAW writer/editor (ADR 10.5)."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

_lock_registry: dict[str, threading.Lock] = {}
_registry_guard = threading.Lock()


def _lock_key(task_id: str, chapter_index: int | None, capability: str) -> str:
    ch = chapter_index if chapter_index is not None else 0
    return f"{task_id}:ch{ch}:{capability}"


def _get_lock(key: str) -> threading.Lock:
    with _registry_guard:
        if key not in _lock_registry:
            _lock_registry[key] = threading.Lock()
        return _lock_registry[key]


@contextmanager
def chapter_artifact_lock(
    task_id: str,
    chapter_index: int | None,
    capability: str,
) -> Iterator[None]:
    """Serialize writer/editor on shared manuscript files for one chapter."""
    if capability not in ("write_chapter", "polish_chapter", "append_body", "write_body"):
        yield
        return
    lock = _get_lock(_lock_key(task_id, chapter_index, "manuscript_write"))
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
