"""Shared execution control protocol for long-running task paths."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.task_control import (
    TaskControl,
    WorkerControl,
    snapshot_task_control,
    snapshot_worker_control,
)

# Control sub-states stored on live snapshot and interrupt_context
CONTROL_IDLE = "IDLE"
CONTROL_RUNNING_STEP = "RUNNING_STEP"
CONTROL_STREAMING_OUTPUT = "STREAMING_OUTPUT"
CONTROL_COMMITTING_STEP = "COMMITTING_STEP"
CONTROL_INTERRUPT_REQUESTED = "INTERRUPT_REQUESTED"
CONTROL_REPLANNING = "REPLANNING"
CONTROL_CANCELLING = "CANCELLING"
CONTROL_PAUSE_REQUESTED = "PAUSE_REQUESTED"
CONTROL_PAUSED_AT_CHECKPOINT = "PAUSED_AT_CHECKPOINT"
CONTROL_CANCEL_REQUESTED = "CANCEL_REQUESTED"
CONTROL_CANCELLED_DURING_STEP = "CANCELLED_DURING_STEP"
CONTROL_FAILED_DURING_STEP = "FAILED_DURING_STEP"


class StreamInterrupted(Exception):
    """SSE output should stop; task lifecycle continues."""


class PauseRequested(Exception):
    """Pause requested; not a failure."""


class CancelRequested(Exception):
    """Cancel requested; not a dead letter."""


# Step kinds that allow partial commit at paragraph/section boundaries
_PARTIAL_COMMIT_KINDS = frozenset({"append_body", "write_outline"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def empty_interrupt_context() -> dict[str, Any]:
    return {
        "pause_requested": False,
        "cancel_requested": False,
        "last_control_event": None,
        "active_step": None,
        "last_committed_step": None,
        "resume_from_checkpoint": None,
        "control_state": CONTROL_IDLE,
        "foreground_epoch": 0,
        "worker_controls": {},
    }


def ensure_interrupt_context(state: AgentState) -> dict[str, Any]:
    ctx = dict(state.get("interrupt_context") or {})
    base = empty_interrupt_context()
    base.update(ctx)
    return base


def effective_control_state(control: TaskControl | None) -> str:
    if control is None:
        return CONTROL_IDLE
    if control.cancel_requested:
        return CONTROL_CANCEL_REQUESTED
    if control.pause_requested:
        return CONTROL_PAUSE_REQUESTED
    if control.stream_interrupted:
        return CONTROL_STREAMING_OUTPUT
    return CONTROL_RUNNING_STEP


def resolve_worker_id(state: AgentState, *, explicit: str = "") -> str:
    """Resolve OMAW worker scope id from state or explicit override."""
    if explicit:
        return str(explicit)
    payload = state.get("input_payload") or {}
    item = payload.get("current_work_item") or {}
    return str(item.get("id") or payload.get("oma_capability") or "default")


def _worker_cancel_pause(
    worker: WorkerControl | None,
    *,
    raise_on_pause: bool,
    raise_on_cancel: bool,
) -> bool:
    if worker is None:
        return False
    if worker.cancel_requested:
        if raise_on_cancel:
            raise CancelRequested(worker.reason or "worker cancel requested")
        return True
    if worker.pause_requested:
        if raise_on_pause:
            raise PauseRequested(worker.reason or "worker pause requested")
        return True
    return False


def check_for_control_signal(
    task_id: str,
    *,
    worker_id: str | None = None,
    phase: str = "",
    step_epoch: int | None = None,
    raise_on_pause: bool = False,
    raise_on_cancel: bool = False,
    raise_on_stream_interrupt: bool = False,
    raise_on_epoch_stale: bool = False,
) -> TaskControl | None:
    """
    Inspect in-process control registry (task-scope and optional worker-scope).

    When raise_on_* flags are set, raises the corresponding control exception.
    Worker-scope signals take precedence when worker_id is provided.
    """
    if worker_id:
        worker = snapshot_worker_control(str(task_id), str(worker_id))
        if _worker_cancel_pause(
            worker,
            raise_on_pause=raise_on_pause,
            raise_on_cancel=raise_on_cancel,
        ):
            return snapshot_task_control(str(task_id))

    control = snapshot_task_control(str(task_id))
    if control is None:
        return None
    if control.cancel_requested:
        if raise_on_cancel:
            raise CancelRequested(control.reason or "cancel requested")
        return control
    if control.pause_requested:
        if raise_on_pause:
            raise PauseRequested(control.reason or "pause requested")
        return control
    if control.stream_interrupted and raise_on_stream_interrupt:
        raise StreamInterrupted(control.reason or "stream interrupted")
    if raise_on_epoch_stale and step_epoch is not None:
        from app.services.foreground_execution import EpochStale

        if int(control.foreground_epoch or 0) > int(step_epoch):
            raise EpochStale(
                f"foreground epoch {control.foreground_epoch} > step {step_epoch} ({phase})"
            )
    return control


def raise_if_cancel_requested(task_id: str, *, phase: str = "") -> None:
    check_for_control_signal(
        str(task_id),
        phase=phase,
        raise_on_cancel=True,
    )


def should_pause_at_boundary(task_id: str, step_kind: str = "") -> bool:
    control = snapshot_task_control(str(task_id))
    if control is None:
        return False
    return bool(control.pause_requested or control.cancel_requested)


def allows_partial_commit(step_kind: str, *, boundary: str = "") -> bool:
    if step_kind == "append_body":
        return boundary in ("", "paragraph")
    if step_kind == "write_outline":
        return boundary == "outline_block"
    return step_kind in _PARTIAL_COMMIT_KINDS and not boundary


def mark_step_boundary(
    state: AgentState,
    step_meta: dict[str, Any],
    *,
    control_state: str = CONTROL_RUNNING_STEP,
) -> AgentState:
    """Record active step metadata in interrupt_context."""
    ctx = ensure_interrupt_context(state)
    ctx["active_step"] = dict(step_meta)
    ctx["control_state"] = control_state
    return merge_state(state, interrupt_context=ctx)


def record_control_event(
    state: AgentState,
    event: str,
    *,
    detail: dict[str, Any] | None = None,
) -> AgentState:
    ctx = ensure_interrupt_context(state)
    ctx["last_control_event"] = {
        "event": event,
        "at": _now_iso(),
        "detail": detail or {},
    }
    return merge_state(
        state,
        interrupt_context=ctx,
        audit_log=append_audit(state, "task_control", event, detail or {}),
    )


def persist_control_request_to_state(
    state: AgentState,
    *,
    pause: bool = False,
    cancel: bool = False,
    stream_interrupt: bool = False,
    requested_by: str = "web",
    reason: str = "user_requested",
    worker_id: str | None = None,
) -> AgentState:
    """Persist control intent for cross-process / page-refresh recovery."""
    ctx = ensure_interrupt_context(state)
    if worker_id:
        worker_controls = dict(ctx.get("worker_controls") or {})
        row = dict(worker_controls.get(str(worker_id)) or {})
        if pause:
            row["pause_requested"] = True
        if cancel:
            row["cancel_requested"] = True
        row["requested_by"] = requested_by
        row["reason"] = reason
        row["updated_at"] = _now_iso()
        worker_controls[str(worker_id)] = row
        ctx["worker_controls"] = worker_controls
    if pause:
        ctx["pause_requested"] = True
        ctx["control_state"] = CONTROL_PAUSE_REQUESTED
    if cancel:
        ctx["cancel_requested"] = True
        ctx["control_state"] = CONTROL_CANCEL_REQUESTED
    if stream_interrupt:
        ctx["control_state"] = CONTROL_STREAMING_OUTPUT
    ctx["last_control_event"] = {
        "event": "pause" if pause else ("cancel" if cancel else "interrupt_stream"),
        "at": _now_iso(),
        "requested_by": requested_by,
        "reason": reason,
        "worker_id": worker_id,
    }
    return merge_state(state, interrupt_context=ctx)


def mark_step_committed(
    state: AgentState,
    step_meta: dict[str, Any],
    *,
    checkpoint_ref: str | None = None,
) -> AgentState:
    ctx = ensure_interrupt_context(state)
    committed = dict(step_meta)
    committed["status"] = "committed"
    committed["updated_at"] = _now_iso()
    if checkpoint_ref:
        committed["checkpoint_ref"] = checkpoint_ref
    ctx["last_committed_step"] = committed
    ctx["active_step"] = None
    ctx["resume_from_checkpoint"] = {
        "step_id": committed.get("step_id"),
        "checkpoint_ref": checkpoint_ref,
        "committed_at": committed["updated_at"],
    }
    ctx["control_state"] = CONTROL_IDLE
    ctx["pause_requested"] = False
    ctx["cancel_requested"] = False
    updated = merge_state(state, interrupt_context=ctx)
    return update_work_plan_checkpoint(updated, committed, checkpoint_ref=checkpoint_ref)


def update_work_plan_checkpoint(
    state: AgentState,
    step_meta: dict[str, Any],
    *,
    checkpoint_ref: str | None = None,
) -> AgentState:
    """Attach checkpoint metadata to matching work_plan item."""
    progress = dict(state.get("progress") or {})
    work_plan = progress.get("work_plan")
    if not isinstance(work_plan, dict):
        return state
    items = work_plan.get("items")
    if not isinstance(items, list):
        return state
    step_id = str(step_meta.get("step_id") or "")
    work_item_id = str(step_meta.get("work_item_id") or "")
    run_meta = state.get("execution_run") if isinstance(state.get("execution_run"), dict) else {}
    run_id = str(step_meta.get("last_run_id") or run_meta.get("run_id") or "")
    attempt = int(step_meta.get("attempt") or 0)
    updated_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            updated_items.append(item)
            continue
        row = dict(item)
        item_id = str(row.get("id") or "")
        if work_item_id and item_id == work_item_id:
            row["last_step_id"] = step_id or row.get("last_step_id")
            row["committed"] = True
            if checkpoint_ref:
                row["checkpoint_ref"] = checkpoint_ref
            if run_id:
                row["last_run_id"] = run_id
            if attempt:
                row["attempt"] = attempt
            row["status"] = row.get("status") if row.get("status") == "cancelled" else "done"
        updated_items.append(row)
    work_plan = {**work_plan, "items": updated_items}
    progress["work_plan"] = work_plan
    return merge_state(state, progress=progress)


def finalize_control_outcome(state: AgentState, control: TaskControl | None) -> AgentState:
    """
    Settle pause/cancel at graph turn end.

    Pause → MISSION_PAUSED with user_requested_pause reason.
    Cancel → CANCELLED, preserve committed artifacts.
    """
    from app.services.mission_execution import (
        PAUSE_USER_REQUESTED_CANCEL,
        PAUSE_USER_REQUESTED_PAUSE,
    )

    ctx = ensure_interrupt_context(state)
    paused = bool((control and control.pause_requested) or ctx.get("pause_requested"))
    cancelled = bool((control and control.cancel_requested) or ctx.get("cancel_requested"))

    if cancelled:
        updated = merge_state(
            state,
            status=TaskStatus.CANCELLED.value,
            mission_control={
                "pause_reason": PAUSE_USER_REQUESTED_CANCEL,
                "reason": control.reason if control else ctx.get("last_control_event", {}).get("reason"),
                "at": _now_iso(),
            },
        )
        ctx["control_state"] = CONTROL_CANCELLED_DURING_STEP
        ctx["cancel_requested"] = False
        return record_control_event(
            merge_state(updated, interrupt_context=ctx),
            "task_cancelled",
        )

    if paused:
        updated = merge_state(
            state,
            status=TaskStatus.MISSION_PAUSED.value,
            mission_control={
                "pause_reason": PAUSE_USER_REQUESTED_PAUSE,
                "reason": control.reason if control else ctx.get("last_control_event", {}).get("reason"),
                "at": _now_iso(),
            },
        )
        ctx["control_state"] = CONTROL_PAUSED_AT_CHECKPOINT
        ctx["pause_requested"] = False
        return record_control_event(
            merge_state(updated, interrupt_context=ctx),
            "task_paused_at_checkpoint",
        )

    return state


def controlled_iter(task_id: str, chunks: Iterator[str], *, phase: str = "stream") -> Iterator[str]:
    """Wrap a text stream iterator with periodic control checks."""
    for chunk in chunks:
        check_for_control_signal(
            str(task_id),
            phase=phase,
            raise_on_pause=True,
            raise_on_cancel=True,
        )
        control = snapshot_task_control(str(task_id))
        if control and control.stream_interrupted:
            raise StreamInterrupted("stream output interrupted")
        yield chunk


def build_control_response(
    task_id: str,
    *,
    control_action: str,
    accepted: bool,
    control: TaskControl | None = None,
    worker_control: WorkerControl | None = None,
    active_step_id: str | None = None,
    worker_id: str | None = None,
) -> dict[str, Any]:
    if worker_control is not None:
        if worker_control.cancel_requested:
            effective = CONTROL_CANCEL_REQUESTED
        elif worker_control.pause_requested:
            effective = CONTROL_PAUSE_REQUESTED
        else:
            effective = CONTROL_IDLE
    else:
        effective = effective_control_state(control)
    return {
        "task_id": task_id,
        "accepted": accepted,
        "control_action": control_action,
        "effective_state": effective,
        "active_step_id": active_step_id,
        "run_id": control.run_id if control else None,
        "worker_id": worker_id or (worker_control.worker_id if worker_control else None),
    }


def is_step_already_committed(state: AgentState, checkpoint_ref: str) -> bool:
    ctx = ensure_interrupt_context(state)
    last = ctx.get("last_committed_step") or {}
    return str(last.get("checkpoint_ref") or "") == str(checkpoint_ref)


def apply_checkpoint_to_resume_state(state: AgentState) -> AgentState:
    """
    Resume from last committed checkpoint: clear control flags, skip committed work items.

    Called by prepare_resume_mission before execution_grant.
    """
    ctx = ensure_interrupt_context(state)
    ctx["pause_requested"] = False
    ctx["cancel_requested"] = False
    ctx["control_state"] = CONTROL_IDLE

    last = ctx.get("last_committed_step") if isinstance(ctx.get("last_committed_step"), dict) else {}
    resume = ctx.get("resume_from_checkpoint") if isinstance(ctx.get("resume_from_checkpoint"), dict) else {}
    checkpoint_ref = resume.get("checkpoint_ref") or last.get("checkpoint_ref")
    step_id = resume.get("step_id") or last.get("step_id")
    work_item_id = last.get("work_item_id")

    payload = dict(state.get("input_payload") or {})
    if checkpoint_ref:
        payload["resume_checkpoint_ref"] = str(checkpoint_ref)
    if step_id:
        payload["resume_from_step_id"] = str(step_id)
    payload.pop("writing_stopped_for_steer", None)

    progress = dict(state.get("progress") or {})
    work_plan = progress.get("work_plan")
    if isinstance(work_plan, dict) and work_item_id:
        items: list[dict[str, Any]] = []
        for raw in work_plan.get("items") or []:
            if not isinstance(raw, dict):
                items.append(raw)
                continue
            row = dict(raw)
            if str(row.get("id") or "") == str(work_item_id) and row.get("committed"):
                row["status"] = "done"
            items.append(row)
        completed = list(work_plan.get("completed_ids") or [])
        wid = str(work_item_id)
        if wid and wid not in completed and any(
            str(i.get("id") or "") == wid and i.get("committed") for i in items if isinstance(i, dict)
        ):
            completed.append(wid)
        work_plan = {**work_plan, "items": items, "completed_ids": completed, "current_id": None}
        progress["work_plan"] = work_plan

    updated = merge_state(
        state,
        input_payload=payload,
        progress=progress,
        interrupt_context=ctx,
    )
    try:
        from app.services.metrics_service import get_metrics_service

        get_metrics_service().inc_resume_from_checkpoint()
    except Exception:
        pass
    return record_control_event(
        updated,
        "resume_from_checkpoint",
        detail={"checkpoint_ref": checkpoint_ref, "step_id": step_id},
    )


def observe_control_executed(
    task_id: str,
    control: TaskControl | None,
    *,
    started_at_iso: str | None,
    event: str,
) -> None:
    """Record control latency metrics when executor observes a control signal."""
    if control is None or not control.requested_at:
        return
    try:
        from datetime import datetime

        from app.services.metrics_service import get_metrics_service

        requested = datetime.fromisoformat(control.requested_at.replace("Z", "+00:00"))
        observed = datetime.now(requested.tzinfo or timezone.utc)
        latency_ms = max(0, int((observed - requested).total_seconds() * 1000))
        metrics = get_metrics_service()
        if event == "pause":
            metrics.observe_pause_latency_ms(latency_ms)
            metrics.inc_task_control_event("task_pause_observed")
        elif event == "cancel":
            metrics.observe_cancel_latency_ms(latency_ms)
            metrics.inc_task_control_event("task_cancel_observed")
        elif event == "stream_interrupt":
            metrics.inc_task_control_event("stream_interrupted")
    except Exception:
        pass


def handle_control_exception(state: AgentState, exc: BaseException) -> AgentState | None:
    """
    Map control exceptions to mission pause/cancel — not failure/dead letter.

    Returns updated state or None when exc is not a control signal.
    """
    from app.services.mission_execution import (
        PAUSE_USER_REQUESTED_CANCEL,
        PAUSE_USER_REQUESTED_PAUSE,
    )

    if isinstance(exc, PauseRequested):
        observe_control_executed(str(state["task_id"]), snapshot_task_control(str(state["task_id"])), started_at_iso=None, event="pause")
        return merge_state(
            state,
            status=TaskStatus.MISSION_PAUSED.value,
            mission_control={"pause_reason": PAUSE_USER_REQUESTED_PAUSE, "reason": str(exc)},
            audit_log=append_audit(state, "task_control", "task_pause_observed", {"detail": str(exc)}),
        )
    if isinstance(exc, CancelRequested):
        observe_control_executed(str(state["task_id"]), snapshot_task_control(str(state["task_id"])), started_at_iso=None, event="cancel")
        return merge_state(
            state,
            status=TaskStatus.CANCELLED.value,
            mission_control={"pause_reason": PAUSE_USER_REQUESTED_CANCEL, "reason": str(exc)},
            audit_log=append_audit(state, "task_control", "task_cancel_observed", {"detail": str(exc)}),
        )
    if isinstance(exc, StreamInterrupted):
        return record_control_event(state, "stream_interrupted", detail={"detail": str(exc)})
    return None
