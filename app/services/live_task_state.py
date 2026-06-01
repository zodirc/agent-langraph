"""In-process live AgentState for tasks currently executing on this worker."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.runtime.state import AgentState, ensure_agent_state


@dataclass
class LiveTaskEntry:
    state: AgentState
    updated_at: str
    running: bool = True


_lock = threading.Lock()
_live_by_task: dict[str, LiveTaskEntry] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def register_live(state: AgentState) -> None:
    """Mark task as actively streaming on this process."""
    task_id = str(state["task_id"])
    normalized = ensure_agent_state(state)
    with _lock:
        _live_by_task[task_id] = LiveTaskEntry(
            state=normalized,
            updated_at=_now_iso(),
            running=True,
        )


def touch_live(state: AgentState) -> None:
    """Update live snapshot when graph emits a new state chunk."""
    task_id = str(state["task_id"])
    normalized = ensure_agent_state(state)
    with _lock:
        entry = _live_by_task.get(task_id)
        if entry is None or not entry.running:
            return
        entry.state = normalized
        entry.updated_at = _now_iso()


def touch_live_if_active(task_id: str, state: AgentState) -> None:
    """Update live after StateStore.save during an in-flight stream (e.g. writing phases)."""
    normalized = ensure_agent_state(state)
    with _lock:
        entry = _live_by_task.get(str(task_id))
        if entry is None or not entry.running:
            return
        entry.state = normalized
        entry.updated_at = _now_iso()


def clear_live(task_id: str) -> None:
    with _lock:
        _live_by_task.pop(str(task_id), None)


def get_live(task_id: str) -> Optional[LiveTaskEntry]:
    with _lock:
        entry = _live_by_task.get(str(task_id))
        if entry is None:
            return None
        return LiveTaskEntry(
            state=dict(entry.state),
            updated_at=entry.updated_at,
            running=entry.running,
        )


def clear_all_live_for_tests() -> None:
    with _lock:
        _live_by_task.clear()
