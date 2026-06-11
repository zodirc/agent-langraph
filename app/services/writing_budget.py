"""Write-action budget for manuscript turns (reads are free)."""

from __future__ import annotations

from typing import Any, Mapping

_WRITE_TOOLS = frozenset(
    {"write_text_artifact", "append_text_artifact", "edit_text_artifact"}
)


def count_write_actions(tool_results: list[dict[str, Any]]) -> int:
    """Count write/append/edit attempts this turn (including failed) to cap replan loops."""
    count = 0
    for item in tool_results:
        tool = str(item.get("tool") or "")
        if tool in _WRITE_TOOLS:
            count += 1
    return count


def max_write_actions_for_state(state: Mapping[str, Any] | dict[str, Any]) -> int:
    """Return configured max write actions; 0 means unlimited."""
    payload = state.get("input_payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    emc = payload.get("effective_mode_contract") or {}
    if isinstance(emc, dict):
        execution = emc.get("execution") or {}
        if isinstance(execution, dict) and execution.get("max_write_actions") is not None:
            return max(0, int(execution.get("max_write_actions") or 0))

    mode = str(payload.get("target_mode") or "")
    if mode:
        from app.services.mode_registry import get_mode_contract

        contract = get_mode_contract(mode)
        if contract is not None:
            return max(0, int(contract.execution.max_write_actions or 0))
    return 0


def write_budget_exhausted(
    state: Mapping[str, Any] | dict[str, Any],
    tool_results: list[dict[str, Any]],
) -> bool:
    limit = max_write_actions_for_state(state)
    if limit <= 0:
        return False
    return count_write_actions(tool_results) >= limit
