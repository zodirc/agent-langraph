"""Interrupt control state machine (optimization WP-1.3, §6.2)."""

from __future__ import annotations

from typing import Any, Literal

from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.execution_control import (
    CONTROL_IDLE,
    CONTROL_INTERRUPT_REQUESTED,
    CONTROL_REPLANNING,
    ensure_interrupt_context,
    record_control_event,
)

# Standard runtime control states (optimization §6.2)
RUNTIME_RUNNING = "RUNNING"
RUNTIME_INTERRUPT_REQUESTED = "INTERRUPT_REQUESTED"
RUNTIME_INTERRUPTED = "INTERRUPTED"
RUNTIME_REPLANNING = "REPLANNING"
RUNTIME_RESUMABLE_PLAN_READY = "RESUMABLE_PLAN_READY"
RUNTIME_ABORTED = "ABORTED"

InterruptRoute = Literal[
    "continue",
    "replanning",
    "resumable",
    "aborted",
]


def _runtime_state(ctx: dict[str, Any]) -> str:
    return str(ctx.get("runtime_state") or ctx.get("control_state") or CONTROL_IDLE)


def resolve_interrupt_route(state: AgentState) -> InterruptRoute:
    """Decide interrupt_control routing from event + interrupt_context."""
    event_type = str(state.get("event_type") or "")
    ctx = ensure_interrupt_context(state)
    runtime = _runtime_state(ctx)
    payload = state.get("input_payload") or {}
    goal = str(payload.get("goal") or payload.get("query") or "").strip()
    from app.services.interaction_goal import goal_is_session_source_inquiry

    if event_type == "clarification" and goal_is_session_source_inquiry(goal):
        return "continue"

    if event_type == "interrupt" or ctx.get("cancel_requested"):
        if ctx.get("abort_requested"):
            return "aborted"
        return "replanning"

    if event_type == "resume" or ctx.get("resume_from_checkpoint"):
        return "resumable"

    if runtime in {CONTROL_REPLANNING, RUNTIME_REPLANNING, RUNTIME_INTERRUPTED}:
        if event_type in {"redirect", "clarification", "reject"}:
            return "replanning"
        if event_type == "confirm":
            return "resumable"

    return "continue"


def apply_interrupt_control(state: AgentState) -> AgentState:
    """Apply interrupt state transitions and stamp runtime_state on interrupt_context."""
    route = resolve_interrupt_route(state)
    ctx = ensure_interrupt_context(state)
    event_type = str(state.get("event_type") or "")

    if route == "aborted":
        ctx["runtime_state"] = RUNTIME_ABORTED
        ctx["control_state"] = CONTROL_IDLE
        ctx["cancel_requested"] = True
        updated = merge_state(
            state,
            interrupt_context=ctx,
            status=TaskStatus.CANCELLED.value,
            current_node="interrupt_control",
            audit_log=append_audit(
                state,
                "interrupt_control",
                "aborted",
                {"event_type": event_type},
            ),
        )
        return record_control_event(updated, "runtime_aborted", detail={"event_type": event_type})

    if route == "replanning":
        if event_type == "interrupt":
            ctx["runtime_state"] = RUNTIME_INTERRUPT_REQUESTED
            ctx["control_state"] = CONTROL_INTERRUPT_REQUESTED
        else:
            ctx["runtime_state"] = RUNTIME_REPLANNING
            ctx["control_state"] = CONTROL_REPLANNING
        if event_type == "interrupt":
            ctx["runtime_state"] = RUNTIME_INTERRUPTED
        payload = dict(state.get("input_payload") or {})
        payload["require_planning_after_steer"] = True
        updated = merge_state(
            state,
            input_payload=payload,
            interrupt_context=ctx,
            current_node="interrupt_control",
            audit_log=append_audit(
                state,
                "interrupt_control",
                "replanning",
                {"event_type": event_type, "runtime_state": ctx["runtime_state"]},
            ),
        )
        return record_control_event(
            updated,
            "runtime_replanning",
            detail={"event_type": event_type},
        )

    if route == "resumable":
        ctx["runtime_state"] = RUNTIME_RESUMABLE_PLAN_READY
        ctx["control_state"] = CONTROL_IDLE
        ctx["pause_requested"] = False
        ctx["cancel_requested"] = False
        from app.services.structured_checkpoint import restore_from_structured_checkpoint

        restored = restore_from_structured_checkpoint(
            merge_state(
                state,
                interrupt_context=ctx,
            )
        )
        restored_ctx = ensure_interrupt_context(restored)
        restored_ctx["runtime_state"] = RUNTIME_RESUMABLE_PLAN_READY
        updated = merge_state(
            restored,
            interrupt_context=restored_ctx,
            current_node="interrupt_control",
            audit_log=append_audit(
                state,
                "interrupt_control",
                "resumable",
                {"event_type": event_type},
            ),
        )
        return record_control_event(
            updated,
            "runtime_resumable",
            detail={"event_type": event_type},
        )

    ctx["runtime_state"] = RUNTIME_RUNNING
    if str(ctx.get("control_state") or "") not in {
        CONTROL_REPLANNING,
        CONTROL_INTERRUPT_REQUESTED,
    }:
        ctx["control_state"] = CONTROL_IDLE
    return merge_state(
        state,
        interrupt_context=ctx,
        current_node="interrupt_control",
        audit_log=append_audit(
            state,
            "interrupt_control",
            "continue",
            {"event_type": event_type, "runtime_state": ctx["runtime_state"]},
        ),
    )


def should_abort_after_interrupt(state: AgentState) -> bool:
    ctx = ensure_interrupt_context(state)
    return str(ctx.get("runtime_state") or "") == RUNTIME_ABORTED
