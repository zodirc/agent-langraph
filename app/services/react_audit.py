"""Audit events and metrics for Self-Routed Deliberation Loop (SRDL)."""

from __future__ import annotations

from typing import Any, Optional

from app.runtime.state import AgentState
from app.services.turn_event_log import record_turn_event

REACT_EVENT_TYPES = frozenset(
    {
        "react_step_started",
        "react_action_selected",
        "react_action_blocked",
        "react_action_executed",
        "react_observation_recorded",
        "react_replanned",
        "react_loop_finished",
        "react_loop_aborted",
        "react_route_recommended",
        "react_runtime_upgrade_suggested",
    }
)


def record_react_event(
    state: AgentState,
    event_type: str,
    subject: str,
    node: str,
    detail: Optional[dict[str, Any]] = None,
    *,
    caused_by: Optional[str] = None,
) -> AgentState:
    if event_type not in REACT_EVENT_TYPES:
        event_type = f"react_{event_type}"
    if event_type == "react_action_blocked":
        from app.services.metrics_service import get_metrics_service

        get_metrics_service().inc_react_action_blocked(subject)
    return record_turn_event(
        state,
        event_type,
        subject,
        node,
        detail,
        caused_by=caused_by,
    )


def export_react_loop_prometheus(state: AgentState) -> None:
    """Push SRDL outcome metrics to Prometheus (no-op when metrics disabled)."""
    from app.config.settings import settings
    from app.services.metrics_service import get_metrics_service

    if not getattr(settings, "METRICS_ENABLED", True):
        return
    summary = react_metrics_from_state(state)
    loop = state.get("react_loop") or {}
    get_metrics_service().record_react_loop_outcome(
        status=str(summary.get("loop_status") or loop.get("status") or "unknown"),
        exit_path=str(summary.get("exit_path") or loop.get("exit_path") or "unknown"),
        step_count=int(summary.get("react_step_count") or 0),
        action_distribution=summary.get("action_distribution"),
        replan_count=int(summary.get("replan_count") or 0),
        runtime_upgrade=loop.get("pending_runtime_upgrade"),
    )


def react_metrics_from_state(state: AgentState) -> dict[str, Any]:
    """Summarize react loop metrics from turn event log."""
    from app.services.turn_event_log import get_turn_event_log

    log = get_turn_event_log(state)
    react_events = [e for e in log.events if e.event_type.startswith("react_")]
    action_counts: dict[str, int] = {}
    for ev in react_events:
        if ev.event_type == "react_action_selected":
            action = str((ev.detail or {}).get("action") or "unknown")
            action_counts[action] = action_counts.get(action, 0) + 1

    loop = state.get("react_loop") or {}
    history = loop.get("history") or []
    return {
        "react_event_count": len(react_events),
        "react_step_count": len(history),
        "action_distribution": action_counts,
        "loop_status": loop.get("status"),
        "exit_reason": loop.get("exit_reason"),
        "exit_path": loop.get("exit_path"),
        "replan_count": loop.get("replan_count", 0),
    }
