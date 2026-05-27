"""
Steer intent confirmation — after planning interprets user steer, pause for explicit OK
before mission_act continues (confirm-before-execute for material changes).
"""

from __future__ import annotations

from typing import Any, Optional

from app.runtime.state import AgentState, TaskStatus, merge_state
from app.services.confirmation.block_builder import build_intent_confirmation_block
from app.services.confirmation.gate_registry import GateContext, intent_gate_required


def steer_confirmation_pending(payload: dict[str, Any]) -> bool:
    return payload.get("steer_intent_pending_confirm") is True


def steer_confirmation_required(
    planning_result: dict[str, Any],
    payload: dict[str, Any],
    *,
    mission_before: Optional[dict[str, Any]] = None,
) -> bool:
    """True when steer planning produced a material change worth user OK before act."""
    return intent_gate_required(
        GateContext(
            planning_result=planning_result,
            payload=payload,
            mission_before=mission_before,
        )
    )


def build_steer_intent_summary(
    planning_result: dict[str, Any],
    payload: dict[str, Any],
    *,
    mission_before: Optional[dict[str, Any]] = None,
    state: Optional[dict[str, Any]] = None,
    task_id: Optional[str] = None,
) -> dict[str, Any]:
    """Human-facing summary of how planning interpreted the latest steer."""
    tid = task_id or str(payload.get("task_id") or (state or {}).get("task_id") or "")
    return build_intent_confirmation_block(
        planning_result,
        payload,
        state=state,
        mission_before=mission_before,
        task_id=tid or None,
    )


def apply_steer_confirmation_pending(
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
    out["steer_intent_pending_confirm"] = True
    out["steer_intent_confirmed"] = False
    out["steer_intent_confirmation"] = confirmation
    out.pop("steer_intent_confirmed_at", None)
    return out


def clear_steer_confirmation_flags(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    out["steer_intent_pending_confirm"] = False
    out.pop("steer_intent_confirmation", None)
    return out


def confirm_steer_intent(state: AgentState) -> AgentState:
    """User approved planning interpretation — allow mission_act to execute."""
    payload = clear_steer_confirmation_flags(dict(state.get("input_payload") or {}))
    payload["steer_intent_confirmed"] = True
    from datetime import datetime, timezone

    payload["steer_intent_confirmed_at"] = datetime.now(timezone.utc).isoformat()
    return merge_state(
        state,
        input_payload=payload,
        status=TaskStatus.MISSION_RUNNING.value,
        mission_control=None,
    )


def attach_steer_confirmation_to_state(state: AgentState) -> AgentState:
    """Set reasoning summary for pause turn so Web/API can show confirmation block."""
    payload = state.get("input_payload") or {}
    block = payload.get("steer_intent_confirmation") or {}
    text = str(block.get("summary_text") or "请确认本次介入理解后再继续执行。")
    reasoning_result = {
        "summary": text,
        "confidence": 0.9,
        "risk_level": "LOW",
        "structured": {"source": "steer_intent_confirmation", "confirmation": block},
    }
    return merge_state(
        state,
        reasoning_result=reasoning_result,
        final_answer=None,
        status=TaskStatus.REASONED.value,
    )
