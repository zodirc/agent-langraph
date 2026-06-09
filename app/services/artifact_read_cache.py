"""Per-steer read cache for handle_read_text_artifact (optimization.md §3.4)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}


def _cache_key(task_id: str, filename: str, path: Path) -> str:
    try:
        stat = path.stat()
        fingerprint = f"{stat.st_size}:{int(stat.st_mtime_ns)}"
    except OSError:
        fingerprint = "missing"
    return f"{task_id}|{filename}|{fingerprint}"


def get_cached_read(task_id: str, filename: str, path: Path) -> dict[str, Any] | None:
    key = _cache_key(task_id, filename, path)
    with _lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        return dict(entry)


def store_cached_read(task_id: str, filename: str, path: Path, result: dict[str, Any]) -> None:
    key = _cache_key(task_id, filename, path)
    with _lock:
        _cache[key] = dict(result)


def clear_task_read_cache(task_id: str) -> None:
    prefix = f"{task_id}|"
    with _lock:
        for key in list(_cache.keys()):
            if key.startswith(prefix):
                _cache.pop(key, None)


def clear_all_read_cache() -> None:
    with _lock:
        _cache.clear()
