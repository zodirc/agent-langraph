"""Foreground supersede — new user input takes over active mission (Cursor-like).

Distinct from resume_existing: no execution_grant, no checkpoint resume semantics.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.intent_composer import bump_intent_revision

FOREGROUND_KIND_SUPERSEDE = "supersede_with_input"
FOREGROUND_KIND_RESUME = "resume_existing"
FOREGROUND_KIND_STREAM_ONLY = "stream_only"

FG_STATUS_QUEUED = "replan_queued"
FG_STATUS_DISPATCHING = "replan_dispatching"
FG_STATUS_RUNNING = "running"
FG_STATUS_SETTLED = "settled"

PAUSE_SUPERSEDED_BY_INPUT = "superseded_by_new_input"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_op_id() -> str:
    return f"fg_{uuid.uuid4().hex[:12]}"


def foreground_operation(ctx: dict[str, Any]) -> dict[str, Any]:
    op = ctx.get("foreground_operation")
    return dict(op) if isinstance(op, dict) else {}


def record_foreground_operation(
    state: AgentState,
    *,
    kind: str,
    status: str,
    source: str = "user_message",
    intent_revision: Optional[int] = None,
    supersedes_run_id: Optional[str] = None,
) -> AgentState:
    ctx = dict(state.get("interrupt_context") or {})
    prev = foreground_operation(ctx)
    rev = int(intent_revision if intent_revision is not None else prev.get("intent_revision") or 0)
    ctx["foreground_operation"] = {
        "op_id": str(prev.get("op_id") or _new_op_id()),
        "kind": str(kind),
        "intent_revision": rev,
        "source": str(source),
        "status": str(status),
        "supersedes_run_id": supersedes_run_id or prev.get("supersedes_run_id"),
        "created_at": str(prev.get("created_at") or _now_iso()),
        "updated_at": _now_iso(),
    }
    payload = dict(state.get("input_payload") or {})
    payload["active_foreground_revision"] = rev
    payload["work_plan_revision"] = rev
    return merge_state(state, interrupt_context=ctx, input_payload=payload)


def bump_supersede_intent_revision(payload: dict[str, Any]) -> dict[str, Any]:
    return bump_intent_revision(dict(payload))


def is_supersede_replan_pending(payload: dict[str, Any], state: AgentState | None = None) -> bool:
    from app.services.mission_steer import steer_requires_planning

    if steer_requires_planning(payload):
        return True
    if state is not None:
        op = foreground_operation(dict(state.get("interrupt_context") or {}))
        if op.get("kind") == FOREGROUND_KIND_SUPERSEDE and op.get("status") in (
            FG_STATUS_QUEUED,
            FG_STATUS_DISPATCHING,
        ):
            return True
    return False


def is_supersede_replan_dispatch(payload: dict[str, Any], state: AgentState | None = None) -> bool:
    if payload.get("foreground_replan_dispatch"):
        return True
    if state is not None:
        op = foreground_operation(dict(state.get("interrupt_context") or {}))
        return (
            op.get("kind") == FOREGROUND_KIND_SUPERSEDE
            and op.get("status") == FG_STATUS_DISPATCHING
        )
    return False


def mark_supersede_replan_queued(state: AgentState, *, source: str = "api_steer") -> AgentState:
    """After preempt consume: task waits for explicit supersede dispatch (not resume)."""
    payload = bump_supersede_intent_revision(dict(state.get("input_payload") or {}))
    run_meta = state.get("execution_run") if isinstance(state.get("execution_run"), dict) else {}
    updated = merge_state(
        state,
        input_payload=payload,
        status=TaskStatus.MISSION_PAUSED.value,
        mission_control={
            "done": True,
            "action": "pause",
            "reason": "foreground superseded by new input",
            "pause_reason": PAUSE_SUPERSEDED_BY_INPUT,
        },
        audit_log=append_audit(
            state,
            "foreground",
            "supersede_queued",
            {"source": source, "intent_revision": payload.get("intent_revision")},
        ),
    )
    updated = record_foreground_operation(
        updated,
        kind=FOREGROUND_KIND_SUPERSEDE,
        status=FG_STATUS_QUEUED,
        source=source,
        intent_revision=int(payload.get("intent_revision") or 0),
        supersedes_run_id=str(run_meta.get("run_id") or "") or None,
    )
    return updated


def prepare_supersede_replan_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Ready state for planner dispatch — never issues execution_grant."""
    out = dict(payload)
    out["skip_planning_llm"] = False
    out.pop("execution_grant", None)
    out.pop("steer_replan_resume", None)
    out["foreground_replan_dispatch"] = True
    out["steer_planning_done"] = False
    out.pop("require_planning_after_contract_invalidation", None)
    out.pop("_planning_enter_count", None)
    out.pop("_last_planning_contract_sig", None)
    return out


def apply_supersede_dispatch_state(state: AgentState) -> AgentState:
    """Prepare supersede dispatch without checkpoint-resume side effects."""
    from app.services.execution_control import CONTROL_REPLANNING, ensure_interrupt_context

    ctx = ensure_interrupt_context(state)
    ctx["pause_requested"] = False
    ctx["cancel_requested"] = False
    ctx["control_state"] = CONTROL_REPLANNING
    payload = prepare_supersede_replan_payload(dict(state.get("input_payload") or {}))
    payload.pop("writing_stopped_for_steer", None)
    return merge_state(
        state,
        input_payload=payload,
        interrupt_context=ctx,
        mission_control=None,
        reasoning_result=None,
        policy_result=None,
        final_answer=None,
    )


def finalize_steer_for_supersede_replan(
    state: AgentState,
    *,
    source: str = "steer",
    steer_text: str = "",
) -> AgentState:
    """PAUSED-path steer: stage latest input, invalidate plan, queue supersede."""
    from app.services.execution_control import CONTROL_REPLANNING
    from app.services.foreground_execution import (
        enter_replanning_state,
        extract_writing_constraints,
        merge_writing_constraints,
    )
    from app.services.mission_steer import steer_requires_planning

    payload = dict(state.get("input_payload") or {})
    if not steer_requires_planning(payload):
        return state

    text = (steer_text or "").strip()
    if text:
        payload["latest_steer_message"] = text
        payload = merge_writing_constraints(
            payload,
            extract_writing_constraints([text]),
        )

    updated = merge_state(state, input_payload=payload)
    ctx = updated.get("interrupt_context") or {}
    if str(ctx.get("control_state") or "") != CONTROL_REPLANNING:
        hint = str(payload.get("steer_action_hint") or payload.get("steer_replan_mode") or "rewrite")
        updated = enter_replanning_state(updated, action_hint=hint)
    return mark_supersede_replan_queued(updated, source=source)


def settle_foreground_operation(state: AgentState) -> AgentState:
    ctx = dict(state.get("interrupt_context") or {})
    op = foreground_operation(ctx)
    if not op:
        return state
    op["status"] = FG_STATUS_SETTLED
    op["updated_at"] = _now_iso()
    ctx["foreground_operation"] = op
    payload = dict(state.get("input_payload") or {})
    payload.pop("foreground_replan_dispatch", None)
    return merge_state(state, interrupt_context=ctx, input_payload=payload)
