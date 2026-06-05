"""In-process live AgentState for tasks currently executing on this worker."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.runtime.state import AgentState, ensure_agent_state
from app.services.execution_control import CONTROL_IDLE


@dataclass
class LiveTaskEntry:
    state: AgentState
    updated_at: str
    running: bool = True
    run_id: str | None = None
    control_state: str = CONTROL_IDLE
    foreground_epoch: int = 0
    active_step_id: str | None = None
    active_generation_id: str | None = None


_lock = threading.Lock()
_live_by_task: dict[str, LiveTaskEntry] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def register_live(
    state: AgentState,
    *,
    run_id: str | None = None,
    control_state: str = CONTROL_IDLE,
) -> None:
    """Mark task as actively streaming on this process."""
    task_id = str(state["task_id"])
    normalized = ensure_agent_state(state)
    ctx = normalized.get("interrupt_context") or {}
    active = ctx.get("active_step") if isinstance(ctx.get("active_step"), dict) else {}
    with _lock:
        _live_by_task[task_id] = LiveTaskEntry(
            state=normalized,
            updated_at=_now_iso(),
            running=True,
            run_id=run_id,
            control_state=control_state,
            foreground_epoch=int(ctx.get("foreground_epoch") or 0),
            active_step_id=str(active.get("step_id") or "") or None,
            active_generation_id=str(active.get("generation_id") or "") or None,
        )


def touch_live(state: AgentState) -> None:
    """Update live snapshot when graph emits a new state chunk."""
    task_id = str(state["task_id"])
    normalized = ensure_agent_state(state)
    ctx = normalized.get("interrupt_context") or {}
    active = ctx.get("active_step") if isinstance(ctx.get("active_step"), dict) else {}
    with _lock:
        entry = _live_by_task.get(task_id)
        if entry is None or not entry.running:
            return
        entry.state = normalized
        entry.updated_at = _now_iso()
        entry.active_step_id = str(active.get("step_id") or "") or entry.active_step_id
        entry.active_generation_id = (
            str(active.get("generation_id") or "") or entry.active_generation_id
        )
        entry.control_state = str(ctx.get("control_state") or entry.control_state)
        entry.foreground_epoch = int(ctx.get("foreground_epoch") or entry.foreground_epoch)


def touch_live_control(
    task_id: str,
    *,
    control_state: str,
    active_step_id: str | None = None,
    active_generation_id: str | None = None,
) -> None:
    with _lock:
        entry = _live_by_task.get(str(task_id))
        if entry is None:
            return
        entry.control_state = control_state
        if active_step_id is not None:
            entry.active_step_id = active_step_id
        if active_generation_id is not None:
            entry.active_generation_id = active_generation_id
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
            run_id=entry.run_id,
            control_state=entry.control_state,
            foreground_epoch=entry.foreground_epoch,
            active_step_id=entry.active_step_id,
            active_generation_id=entry.active_generation_id,
        )


def clear_all_live_for_tests() -> None:
    with _lock:
        _live_by_task.clear()
