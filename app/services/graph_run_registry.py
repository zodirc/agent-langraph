"""In-process registry of active LangGraph runs (survives SSE client disconnect)."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

_lock = threading.Lock()
_active_run_by_task: dict[str, str] = {}
_run_started_at: dict[str, float] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def begin_graph_run(task_id: str) -> str:
    """Register a new graph run for task_id; returns run_id."""
    run_id = uuid.uuid4().hex
    key = str(task_id)
    with _lock:
        _active_run_by_task[key] = run_id
        _run_started_at[key] = time.monotonic()
    return run_id


def end_graph_run(task_id: str, run_id: str) -> None:
    """Clear registry entry when run_id matches (idempotent)."""
    key = str(task_id)
    with _lock:
        if _active_run_by_task.get(key) == run_id:
            _active_run_by_task.pop(key, None)
            _run_started_at.pop(key, None)


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


def check_turn_wall_clock_budget() -> list[str]:
    """
    Cancel runs exceeding turn_wall_clock_budget_sec.

    Returns list of task_ids that were cancelled.
    """
    from app.config.settings import settings
    from app.services.task_control import request_cancel

    budget = int(getattr(settings, "GRAPH_RUNNER_TURN_WALL_CLOCK_BUDGET_SEC", 480))
    if budget <= 0:
        return []
    now = time.monotonic()
    timed_out: list[str] = []
    with _lock:
        for task_id, started in list(_run_started_at.items()):
            if now - started <= budget:
                continue
            timed_out.append(task_id)
    for task_id in timed_out:
        from app.services.turn_watchdog import finalize_timed_out_task

        finalize_timed_out_task(task_id, reason="turn_wall_clock_budget_exceeded")
    return timed_out


def clear_all_graph_runs_for_tests() -> None:
    with _lock:
        _active_run_by_task.clear()
        _run_started_at.clear()
