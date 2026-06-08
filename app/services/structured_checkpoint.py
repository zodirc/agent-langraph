"""Structured checkpoint for resume (WP-4.1)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.runtime.state import AgentState, merge_state
from app.runtime.state_field_access import progress_from_state
from app.services.execution_control import apply_checkpoint_to_resume_state, ensure_interrupt_context


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def checkpoint_ttl_seconds() -> int:
    return int(getattr(settings, "STRUCTURED_CHECKPOINT_TTL_SECONDS", 604800))


def is_checkpoint_expired(checkpoint: dict[str, Any], *, now: datetime | None = None) -> bool:
    created_raw = str(checkpoint.get("created_at") or "")
    if not created_raw:
        return True
    try:
        created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
    except ValueError:
        return True
    now_dt = now or datetime.now(timezone.utc)
    age = (now_dt - created).total_seconds()
    return age > checkpoint_ttl_seconds()


def build_structured_checkpoint(state: AgentState) -> dict[str, Any]:
    """Capture six recovery layers into a checkpoint object."""
    ctx = ensure_interrupt_context(state)
    progress = progress_from_state(state)
    return {
        "checkpoint_ref": f"{state.get('task_id')}:{state.get('execution_version') or 1}",
        "created_at": _now_iso(),
        "ttl_seconds": checkpoint_ttl_seconds(),
        "execution_version": state.get("execution_version"),
        "working_memory": {
            "progress": progress,
            "plan_graph": state.get("plan_graph"),
        },
        "plan_graph": state.get("plan_graph"),
        "completed_actions": state.get("tool_results"),
        "draft": {
            "reasoning_result": state.get("reasoning_result"),
            "final_answer": state.get("final_answer"),
        },
        "retrieval_cache": {
            "retrieved_knowledge": state.get("retrieved_knowledge"),
            "evidence_packets": state.get("evidence_packets"),
        },
        "tool_observations": state.get("observations") or state.get("observation"),
        "interrupt_context": ctx,
    }


def save_checkpoint_on_state(state: AgentState) -> AgentState:
    checkpoint = build_structured_checkpoint(state)
    ctx = ensure_interrupt_context(state)
    ctx["last_committed_step"] = {
        "checkpoint_ref": checkpoint["checkpoint_ref"],
        "at": checkpoint["created_at"],
    }
    ctx["resume_from_checkpoint"] = checkpoint
    return merge_state(
        state,
        interrupt_context=ctx,
        input_payload={
            **dict(state.get("input_payload") or {}),
            "structured_checkpoint": checkpoint,
        },
    )


def restore_from_structured_checkpoint(state: AgentState) -> AgentState:
    """Apply checkpoint layers for resume events."""
    payload = dict(state.get("input_payload") or {})
    checkpoint = payload.get("structured_checkpoint")
    restored = False
    reason = "missing"

    if not isinstance(checkpoint, dict):
        result = apply_checkpoint_to_resume_state(state)
        restored = True
        reason = "legacy_fallback"
    elif is_checkpoint_expired(checkpoint):
        payload.pop("structured_checkpoint", None)
        result = merge_state(state, input_payload=payload)
        restored = False
        reason = "expired"
    else:
        ctx = ensure_interrupt_context(state)
        ctx["resume_from_checkpoint"] = checkpoint
        wm = checkpoint.get("working_memory") if isinstance(checkpoint.get("working_memory"), dict) else {}
        updates: dict[str, Any] = {
            "interrupt_context": ctx,
            "plan_graph": checkpoint.get("plan_graph") or state.get("plan_graph"),
            "tool_results": checkpoint.get("completed_actions") or state.get("tool_results"),
            "retrieved_knowledge": (checkpoint.get("retrieval_cache") or {}).get("retrieved_knowledge"),
            "evidence_packets": (checkpoint.get("retrieval_cache") or {}).get("evidence_packets"),
            "observation": checkpoint.get("tool_observations") or state.get("observation"),
        }
        progress = wm.get("progress")
        if isinstance(progress, dict):
            bg = dict(state.get("background_status") or {})
            bg["progress"] = progress
            updates["background_status"] = bg
        if checkpoint.get("execution_version") is not None:
            updates["execution_version"] = checkpoint.get("execution_version")
        draft = checkpoint.get("draft") if isinstance(checkpoint.get("draft"), dict) else {}
        if draft.get("reasoning_result"):
            updates["reasoning_result"] = draft["reasoning_result"]
        result = merge_state(state, **updates)
        restored = True
        reason = "structured"

    _record_checkpoint_restore(restored, reason=reason)
    bg = dict(result.get("background_status") or {})
    history = list(bg.get("checkpoint_recovery_history") or [])
    history.append({"restored": restored, "reason": reason})
    bg["checkpoint_recovery"] = checkpoint_recovery_metrics(history)
    return merge_state(result, background_status=bg)


def _record_checkpoint_restore(restored: bool, *, reason: str) -> None:
    try:
        from app.services.metrics_service import get_metrics_service

        get_metrics_service().inc_structured_checkpoint_restore(success=restored, reason=reason)
    except Exception:
        pass


def checkpoint_recovery_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate resume success for observability (WP-4.1 Exit)."""
    attempts = len(results)
    successes = sum(1 for row in results if row.get("restored"))
    return {
        "attempts": attempts,
        "successes": successes,
        "success_rate": (successes / attempts) if attempts else 1.0,
    }
