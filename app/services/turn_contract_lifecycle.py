"""
Turn contract lifecycle — invalidate on control-plane events.

A contract binds one planning→execution cycle. Steer, execution grant, and
non-recoverable step failures retire the current contract and require a fresh
planning pass.

unified-core WP-4: writing/mission conflict reconciliation removed; the
lifecycle is now purely generic (invalidate + replan-required bookkeeping).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.runtime.state import AgentState, TaskStatus, merge_state
from app.services.turn_contract import contract_from_payload

# Invalidation reasons (stored on payload.turn_contract_invalidation.reason)
REASON_STEER = "steer"
REASON_EXECUTION_GRANT = "execution_grant"
REASON_NON_RECOVERABLE_FAILURE = "non_recoverable_failure"
REASON_INCONSISTENT = "contract_inconsistent"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def contract_replan_required(payload: dict[str, Any]) -> bool:
    """True when the retired contract must be replaced by a new planning turn."""
    return bool(payload.get("require_planning_after_contract_invalidation"))


def clear_contract_replan_requirement(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    out.pop("require_planning_after_contract_invalidation", None)
    out.pop("turn_contract_invalidation", None)
    return out


def invalidate_turn_contract_payload(
    payload: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    """
    Retire the active turn_contract and tool routing derived from it.

    The next planning pass establishes a new contract.
    """
    out = dict(payload)
    if (
        reason != REASON_STEER
        and out.get("steer_contract_pinned")
        and out.get("steer_planning_done")
        and reason not in (REASON_NON_RECOVERABLE_FAILURE,)
    ):
        return out
    had_contract = contract_from_payload(out) is not None
    for key in ("turn_contract", "selected_tools", "tool_params", "tool_stages"):
        out.pop(key, None)
        # merge_state shallow-merges input_payload; explicit None clears stale keys.
        out[key] = None
    out["require_planning_after_contract_invalidation"] = True
    out["turn_contract_invalidation"] = {
        "at": _now_iso(),
        "reason": str(reason),
        "had_contract": had_contract,
    }
    return out


def invalidate_turn_contract_state(
    state: AgentState,
    reason: str,
) -> AgentState:
    payload = invalidate_turn_contract_payload(state.get("input_payload") or {}, reason)
    return merge_state(state, input_payload=payload)


def detect_non_recoverable_step_failure(state: AgentState) -> Optional[str]:
    """
    Return a short failure signature when the last step cannot succeed via retry.

    Used to trigger contract invalidation + replan instead of autonomous
    re-execution of the same plan.
    """
    status = str(state.get("status") or "")
    if status in (
        TaskStatus.TOOL_FAILED.value,
        TaskStatus.DEAD_LETTER.value,
    ):
        for err in state.get("errors") or []:
            text = str(err)
            if "non_retryable" in text or "artifact_not_found" in text:
                return text[:240]
        return f"status:{status}"

    for err in state.get("errors") or []:
        text = str(err)
        if "tool_execution(non_retryable)" in text:
            return text[:240]

    for item in state.get("tool_results") or []:
        if not isinstance(item, dict):
            continue
        from app.services.tool_result_helpers import tool_result_flag

        if item.get("non_retryable") or tool_result_flag(item, "non_retryable"):
            return str(item.get("error") or item.get("error_code") or "non_retryable_tool")[
                :240
            ]
        if item.get("error_code") == "artifact_not_found":
            return str(item.get("error") or "artifact_not_found")[:240]

    observation = state.get("observation") or {}
    if observation.get("has_failures"):
        for line in observation.get("tools_executed") or []:
            if not isinstance(line, dict):
                continue
            if line.get("error") and (
                line.get("status") == "error"
                or "not found" in str(line.get("error")).lower()
            ):
                return str(line.get("error"))[:240]
    return None


def apply_non_recoverable_failure_lifecycle(state: AgentState) -> AgentState:
    """
    After a failed execution step: retire contract and require replan.

    Idempotent within the same step when signature unchanged.
    """
    signature = detect_non_recoverable_step_failure(state)
    if not signature:
        return state

    payload = state.get("input_payload") or {}
    prev = payload.get("turn_contract_invalidation") or {}
    if (
        prev.get("reason") == REASON_NON_RECOVERABLE_FAILURE
        and prev.get("signature") == signature
        and contract_replan_required(payload)
    ):
        return state

    payload = invalidate_turn_contract_payload(payload, REASON_NON_RECOVERABLE_FAILURE)
    inv = dict(payload.get("turn_contract_invalidation") or {})
    inv["signature"] = signature
    payload["turn_contract_invalidation"] = inv

    return merge_state(
        state,
        input_payload=payload,
        errors=[signature],
        tool_results=[],
        status=TaskStatus.FAILED.value,
    )
