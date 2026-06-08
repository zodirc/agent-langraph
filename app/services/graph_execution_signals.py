"""Graph execution observability signals (optimization.md §3).

L3 routers use these facts instead of TaskStatus for fork decisions.
Control-plane ingress routing must use session_fsm only.
"""

from __future__ import annotations

from app.runtime.state import AgentState


def graph_turn_had_fatal_error(state: AgentState | dict) -> bool:
    """True when the current graph turn recorded a non-recoverable failure."""
    for err in state.get("errors") or []:
        if str(err or "").strip():
            return True
    log = state.get("audit_log") or []
    if log:
        action = str(log[-1].get("action") or "")
        if action in ("fatal_error", "dead_letter", "non_retryable_error"):
            return True
    return False


def graph_last_tool_failed(state: AgentState | dict) -> bool:
    """True when the most recent tool_result indicates failure."""
    results = state.get("tool_results") or []
    if not results:
        return False
    last = results[-1]
    if not isinstance(last, dict):
        return False
    if str(last.get("status") or "").lower() in ("error", "failed", "skipped"):
        return True
    if last.get("error"):
        return True
    result = last.get("result")
    if isinstance(result, dict) and str(result.get("status") or "").lower() in (
        "error",
        "failed",
    ):
        return True
    return False
