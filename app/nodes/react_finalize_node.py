"""SRDL finalize node — exit bounded loop with explicit reason and path."""

from __future__ import annotations

from app.domain.react_loop import get_react_loop
from app.runtime.state import AgentState, append_audit, merge_state
from app.services.react_loop_runner import finalize_loop, should_abort_loop
from app.services.runtime_router import apply_pending_runtime_upgrade
from app.services.state_store import get_state_store


def react_finalize_node(state: AgentState) -> AgentState:
    """
    Close SRDL with finish_with_answer / degraded / abort exit path.

    Reads: react_loop
    Writes: react_loop.status, exit_reason, exit_path
    """
    loop = get_react_loop(state)
    last_obs = {"action": "finish", "status": "ok"}
    if loop.history:
        last = loop.history[-1]
        last_obs = {
            "action": last.action,
            "status": "failed" if "failed" in last.observation_summary else "ok",
        }

    abort, abort_reason = should_abort_loop(state, loop, last_obs)
    if abort:
        exit_path = "reflection"
        if abort_reason == "max_replan_exceeded":
            exit_path = "back_to_planning"
        if abort_reason == "consecutive_failures" and int(state.get("retry_count") or 0) >= 1:
            exit_path = "dead_letter"
        reason = abort_reason
    elif loop.step_index >= loop.max_steps:
        exit_path = "finish_with_degraded_answer"
        reason = "max_steps_reached"
    else:
        exit_path = "finish_with_answer"
        reason = "enough_information"

    updated = finalize_loop(state, reason=reason, exit_path=exit_path)
    updated = apply_pending_runtime_upgrade(updated)
    updated = merge_state(
        updated,
        current_node="react_finalize",
        audit_log=append_audit(
            updated,
            "react_finalize",
            exit_path,
            {"reason": reason, "steps": loop.step_index},
        ),
    )
    get_state_store().save(updated)
    return updated
