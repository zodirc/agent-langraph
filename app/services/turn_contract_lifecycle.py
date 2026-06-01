"""
Turn contract lifecycle — invalidate on control-plane events (not priority overrides).

A contract binds one planning→execution cycle. Steer, execution grant, and
non-recoverable step failures retire the current contract and require a fresh
planning pass before the next mission_act pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.runtime.state import AgentState, TaskStatus, merge_state
from app.services.turn_contract import (
    _WRITE_ACTIONS,
    contract_blocks_writing,
    contract_from_payload,
    contract_tool_names,
)

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

    Does not rewrite mission step_policy or writing_intent; the next planning
    pass establishes a new contract.
    """
    out = dict(payload)
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


def _effective_writing_action(payload: dict[str, Any], state: AgentState) -> str:
    intent = payload.get("writing_intent") or {}
    if intent.get("enabled"):
        return str(intent.get("action") or intent.get("writing_phase") or "")
    mission = state.get("mission") or payload.get("mission") or {}
    if str(mission.get("kind") or "").lower() == "writing":
        from app.services.mission_schema import resolve_writing_intent_for_step

        resolved = resolve_writing_intent_for_step(state, mission=mission)
        if resolved.get("enabled"):
            return str(resolved.get("action") or "")
    return ""


def contract_conflicts_with_writing_intent(
    payload: dict[str, Any],
    state: AgentState,
) -> Optional[str]:
    """
    Return a reason string when the stored contract cannot execute the active write intent.

    Mechanical check only (forbid list vs enabled action; read-only contract before outline exists).
    """
    contract = contract_from_payload(payload)
    if not contract:
        return None
    primary = str(contract.get("primary_op") or "")
    if primary in ("edit_plot", "review_outline", "run_tools"):
        return None
    action = _effective_writing_action(payload, state)
    if not action or action not in _WRITE_ACTIONS:
        return None
    forbid = {str(x) for x in (contract.get("forbid") or [])}
    if action in forbid:
        return f"contract forbids active writing action {action}"
    tools = set(contract_tool_names(payload))
    if (
        action == "write_outline"
        and contract_blocks_writing(payload)
        and tools <= {"read_text_artifact"}
    ):
        ms = state.get("manuscript") or {}
        outline_bytes = int(ms.get("outline_bytes") or 0)
        if outline_bytes <= 0:
            return "read-only contract before outline artifact exists"
    return None


def sanitize_turn_contract(
    contract: dict[str, Any],
    payload: dict[str, Any],
    state: AgentState,
) -> dict[str, Any]:
    """Repair contract when it contradicts an enabled writing step (same rules as conflict check)."""
    conflict = contract_conflicts_with_writing_intent(
        {**payload, "turn_contract": contract},
        state,
    )
    if not conflict:
        return contract
    action = _effective_writing_action(payload, state)
    return {
        "intent_kind": "forward_write",
        "primary_op": action,
        "ops": [{"op": "write", "action": action}],
        "tools": [],
        "forbid": [],
        "override_step_policy": False,
        "user_visible_reason": f"repaired: {conflict}",
    }


def reconcile_turn_contract_execution(state: AgentState) -> AgentState:
    """Drop contracts that contradict the current writing step (e.g. leftover from pre-mission planning)."""
    payload = state.get("input_payload") or {}
    conflict = contract_conflicts_with_writing_intent(payload, state)
    if not conflict:
        return state
    return invalidate_turn_contract_state(state, REASON_INCONSISTENT)


def detect_non_recoverable_step_failure(state: AgentState) -> Optional[str]:
    """
    Return a short failure signature when the last step cannot succeed via retry.

    Used to trigger contract invalidation + replan instead of autonomous re-execution
    of the same plan.
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
        if item.get("non_retryable") or (item.get("result") or {}).get("non_retryable"):
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
    After a failed mission_act: retire contract, require replan, reset failure streak.

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

    progress = dict(state.get("progress") or {})
    progress["consecutive_failures"] = 0
    progress["last_replan_at"] = _now_iso()
    progress["last_replan_reason"] = signature

    control = dict(state.get("mission_control") or {})
    control["last_replan_trigger"] = REASON_NON_RECOVERABLE_FAILURE

    return merge_state(
        state,
        input_payload=payload,
        progress=progress,
        mission_control=control,
        errors=[signature],
        tool_results=[],
        status=TaskStatus.MISSION_RUNNING.value,
    )
