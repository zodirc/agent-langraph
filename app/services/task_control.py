"""In-process task control registry for pause / cancel / interrupt-stream."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class TaskControl:
    task_id: str
    run_id: str
    stream_interrupted: bool = False
    pause_requested: bool = False
    cancel_requested: bool = False
    requested_at: str | None = None
    requested_by: str | None = None
    reason: str | None = None


@dataclass
class WorkerControl:
    """Worker-scope control (reviewer/editor/planner sub-path)."""

    task_id: str
    worker_id: str
    pause_requested: bool = False
    cancel_requested: bool = False
    requested_at: str | None = None
    requested_by: str | None = None
    reason: str | None = None


_lock = threading.Lock()
_control_by_task: dict[str, TaskControl] = {}
_worker_control_by_task: dict[str, dict[str, WorkerControl]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def register_task_control(task_id: str, run_id: str) -> TaskControl:
    """Register a fresh control handle when a graph run starts."""
    control = TaskControl(task_id=str(task_id), run_id=str(run_id))
    with _lock:
        _control_by_task[str(task_id)] = control
    return control


def _mutate(task_id: str, **updates: object) -> Optional[TaskControl]:
    with _lock:
        control = _control_by_task.get(str(task_id))
        if control is None:
            return None
        for key, value in updates.items():
            setattr(control, key, value)
        if "requested_at" not in updates and any(
            k in updates for k in ("stream_interrupted", "pause_requested", "cancel_requested")
        ):
            control.requested_at = _now_iso()
        return TaskControl(
            task_id=control.task_id,
            run_id=control.run_id,
            stream_interrupted=control.stream_interrupted,
            pause_requested=control.pause_requested,
            cancel_requested=control.cancel_requested,
            requested_at=control.requested_at,
            requested_by=control.requested_by,
            reason=control.reason,
        )


def _mutate_worker(
    task_id: str,
    worker_id: str,
    **updates: object,
) -> Optional[WorkerControl]:
    key = str(task_id)
    wid = str(worker_id)
    with _lock:
        bucket = _worker_control_by_task.setdefault(key, {})
        wc = bucket.get(wid)
        if wc is None:
            wc = WorkerControl(task_id=key, worker_id=wid)
            bucket[wid] = wc
        for field, value in updates.items():
            setattr(wc, field, value)
        if "requested_at" not in updates and any(
            k in updates for k in ("pause_requested", "cancel_requested")
        ):
            wc.requested_at = _now_iso()
        return WorkerControl(
            task_id=wc.task_id,
            worker_id=wc.worker_id,
            pause_requested=wc.pause_requested,
            cancel_requested=wc.cancel_requested,
            requested_at=wc.requested_at,
            requested_by=wc.requested_by,
            reason=wc.reason,
        )


def request_interrupt_stream(
    task_id: str,
    *,
    requested_by: str = "web",
    reason: str = "user_requested",
) -> Optional[TaskControl]:
    """Stop SSE output only; task keeps running."""
    return _mutate(
        str(task_id),
        stream_interrupted=True,
        requested_by=requested_by,
        reason=reason,
    )


def request_pause(
    task_id: str,
    *,
    worker_id: str | None = None,
    requested_by: str = "web",
    reason: str = "user_requested",
) -> Optional[TaskControl | WorkerControl]:
    """Request pause at next safe checkpoint (task or worker scope)."""
    if worker_id:
        return _mutate_worker(
            str(task_id),
            str(worker_id),
            pause_requested=True,
            requested_by=requested_by,
            reason=reason,
        )
    return _mutate(
        str(task_id),
        pause_requested=True,
        requested_by=requested_by,
        reason=reason,
    )


def request_cancel(
    task_id: str,
    *,
    worker_id: str | None = None,
    requested_by: str = "web",
    reason: str = "user_requested",
) -> Optional[TaskControl | WorkerControl]:
    """Request task/worker cancellation."""
    if worker_id:
        return _mutate_worker(
            str(task_id),
            str(worker_id),
            cancel_requested=True,
            requested_by=requested_by,
            reason=reason,
        )
    return _mutate(
        str(task_id),
        cancel_requested=True,
        requested_by=requested_by,
        reason=reason,
    )


def snapshot_task_control(task_id: str) -> Optional[TaskControl]:
    with _lock:
        control = _control_by_task.get(str(task_id))
        if control is None:
            return None
        return TaskControl(
            task_id=control.task_id,
            run_id=control.run_id,
            stream_interrupted=control.stream_interrupted,
            pause_requested=control.pause_requested,
            cancel_requested=control.cancel_requested,
            requested_at=control.requested_at,
            requested_by=control.requested_by,
            reason=control.reason,
        )


def snapshot_worker_control(task_id: str, worker_id: str) -> Optional[WorkerControl]:
    with _lock:
        wc = (_worker_control_by_task.get(str(task_id)) or {}).get(str(worker_id))
        if wc is None:
            return None
        return WorkerControl(
            task_id=wc.task_id,
            worker_id=wc.worker_id,
            pause_requested=wc.pause_requested,
            cancel_requested=wc.cancel_requested,
            requested_at=wc.requested_at,
            requested_by=wc.requested_by,
            reason=wc.reason,
        )


def snapshot_all_worker_controls(task_id: str) -> dict[str, WorkerControl]:
    with _lock:
        bucket = _worker_control_by_task.get(str(task_id)) or {}
        return {
            wid: WorkerControl(
                task_id=wc.task_id,
                worker_id=wc.worker_id,
                pause_requested=wc.pause_requested,
                cancel_requested=wc.cancel_requested,
                requested_at=wc.requested_at,
                requested_by=wc.requested_by,
                reason=wc.reason,
            )
            for wid, wc in bucket.items()
        }


def clear_task_control(task_id: str) -> None:
    with _lock:
        _control_by_task.pop(str(task_id), None)
        _worker_control_by_task.pop(str(task_id), None)


def clear_all_task_control_for_tests() -> None:
    with _lock:
        _control_by_task.clear()
        _worker_control_by_task.clear()
