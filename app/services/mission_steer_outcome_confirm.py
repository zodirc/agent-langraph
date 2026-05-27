"""
Steer outcome confirmation — after a material work item finishes, pause with an artifact
preview so the user can verify results match their steer (confirm-after-execute).
"""

from __future__ import annotations

from typing import Any, Optional

from app.runtime.state import AgentState, TaskStatus, merge_state
from app.services.confirmation.block_builder import build_outcome_confirmation_block
from app.services.confirmation.gate_registry import GateContext, outcome_gate_required
from app.services.confirmation.preview_resolver import resolve_outcome_preview


def steer_outcome_confirmation_pending(payload: dict[str, Any]) -> bool:
    return payload.get("steer_outcome_pending_confirm") is True


def steer_outcome_confirmation_required(
    state: AgentState,
    completed_item: dict[str, Any],
) -> bool:
    payload = state.get("input_payload") or {}
    preview = resolve_outcome_preview(state, completed_item)
    if not preview.content.strip():
        return False
    return outcome_gate_required(
        GateContext(
            planning_result={},
            payload=payload,
            state=state,
            completed_item=completed_item,
            observation=state.get("observation") or {},
        )
    )


def build_steer_outcome_summary(
    state: AgentState,
    completed_item: dict[str, Any],
) -> dict[str, Any]:
    return build_outcome_confirmation_block(state, completed_item)


def apply_steer_outcome_confirmation_pending(
    payload: dict[str, Any],
    confirmation: dict[str, Any],
    *,
    task_id: Optional[str] = None,
) -> dict[str, Any]:
    tid = task_id or str(payload.get("task_id") or "")
    if tid and "user_actions" not in confirmation:
        from app.services.steer_confirmation_actions import enrich_confirmation_block

        confirmation = enrich_confirmation_block(tid, confirmation)
    out = dict(payload)
    if tid:
        out["task_id"] = tid
    out["steer_outcome_pending_confirm"] = True
    out["steer_outcome_confirmed"] = False
    out["steer_outcome_confirmation"] = confirmation
    out.pop("steer_outcome_confirmed_at", None)
    return out


def clear_steer_outcome_flags(payload: dict[str, Any]) -> dict[str, Any]:
    """Explicit False so state_store volatile merge does not resurrect stale gates."""
    out = dict(payload)
    out["steer_outcome_pending_confirm"] = False
    out.pop("steer_outcome_confirmation", None)
    out.pop("steer_outcome_confirmed", None)
    return out


def confirm_steer_outcome(state: AgentState) -> AgentState:
    """User accepted the executed work item — allow mission loop to continue."""
    payload = dict(state.get("input_payload") or {})
    block = payload.get("steer_outcome_confirmation") or {}
    work_item_id = block.get("work_item_id")
    if work_item_id:
        payload["steer_outcome_confirmed_for"] = str(work_item_id)
    batch = payload.get("steer_applied_at")
    batches = list(payload.get("steer_outcome_confirmed_batches") or [])
    if batch and batch not in batches:
        batches.append(batch)
    payload["steer_outcome_confirmed_batches"] = batches
    payload = clear_steer_outcome_flags(payload)
    payload["steer_outcome_confirmed"] = True
    payload.pop("writing_stopped_for_steer", None)
    from datetime import datetime, timezone

    payload["steer_outcome_confirmed_at"] = datetime.now(timezone.utc).isoformat()
    payload.pop("steer_watch_outcome", None)
    return merge_state(
        state,
        input_payload=payload,
        status=TaskStatus.MISSION_RUNNING.value,
        mission_control=None,
    )


def apply_outcome_confirmation_after_work_item(
    state: AgentState,
    completed_item: dict[str, Any],
) -> AgentState:
    if not steer_outcome_confirmation_required(state, completed_item):
        return state
    payload = state.get("input_payload") or {}
    summary = build_steer_outcome_summary(state, completed_item)
    payload = apply_steer_outcome_confirmation_pending(
        payload, summary, task_id=state["task_id"]
    )
    state = merge_state(state, input_payload=payload)
    return attach_steer_outcome_confirmation_to_state(state)


def attach_steer_outcome_confirmation_to_state(state: AgentState) -> AgentState:
    payload = state.get("input_payload") or {}
    block = payload.get("steer_outcome_confirmation") or {}
    text = str(block.get("summary_text") or "请确认本次执行结果后再继续。")
    reasoning_result = {
        "summary": text,
        "confidence": 0.9,
        "risk_level": "LOW",
        "structured": {"source": "steer_outcome_confirmation", "confirmation": block},
    }
    return merge_state(
        state,
        reasoning_result=reasoning_result,
        final_answer=None,
        status=TaskStatus.REASONED.value,
    )
