"""In-process registry of active LangGraph runs (survives SSE client disconnect)."""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

_lock = threading.Lock()
_active_run_by_task: dict[str, str] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def begin_graph_run(task_id: str) -> str:
    """Register a new graph run for task_id; returns run_id."""
    run_id = uuid.uuid4().hex
    with _lock:
        _active_run_by_task[str(task_id)] = run_id
    return run_id


def end_graph_run(task_id: str, run_id: str) -> None:
    """Clear registry entry when run_id matches (idempotent)."""
    key = str(task_id)
    with _lock:
        if _active_run_by_task.get(key) == run_id:
            _active_run_by_task.pop(key, None)


def get_active_run_id(task_id: str) -> Optional[str]:
    with _lock:
        return _active_run_by_task.get(str(task_id))


def is_graph_run_active(task_id: str, run_id: Optional[str] = None) -> bool:
    """True when this process is executing the graph for task_id."""
    active = get_active_run_id(task_id)
    if not active:
        return False
    if run_id and active != run_id:
        return False
    return True


def execution_run_meta(run_id: str) -> dict[str, str]:
    return {"run_id": run_id, "started_at": _now_iso()}


def executor_active_for_state(state: dict[str, Any]) -> bool:
    """True when this process is running the graph for the state's task_id."""
    task_id = str(state.get("task_id") or "")
    if not task_id:
        return False
    run_meta = state.get("execution_run")
    run_id: Optional[str] = None
    if isinstance(run_meta, dict):
        rid = str(run_meta.get("run_id") or "").strip()
        run_id = rid or None
    return is_graph_run_active(task_id, run_id)


def clear_all_graph_runs_for_tests() -> None:
    with _lock:
        _active_run_by_task.clear()
