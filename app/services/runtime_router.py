"""
Runtime-level route recommendation evaluator (Phase 3).

Models may suggest upgrading to mission/supervisor/exploration;
system rules retain final authority — no mid-loop graph switching.
"""

from __future__ import annotations

from typing import Any, Optional

from app.config.settings import settings
from app.domain.react_loop import RUNTIME_RECOMMENDATIONS, RouteRecommendation
from app.runtime.state import AgentState, merge_state
from app.services.react_audit import record_react_event


def evaluate_route_recommendation(
    state: AgentState,
    recommendation: dict[str, Any] | RouteRecommendation | None,
) -> Optional[str]:
    """
    Evaluate a route recommendation. Returns approved runtime name or None.

    None means ignore the suggestion and continue in single runtime.
    """
    if isinstance(recommendation, RouteRecommendation):
        rec = recommendation
    elif isinstance(recommendation, dict):
        rec = RouteRecommendation.from_dict(recommendation)
    else:
        return None

    if rec is None or rec.suggested_runtime not in RUNTIME_RECOMMENDATIONS:
        return None

    min_confidence = float(getattr(settings, "REACT_ROUTE_RECOMMEND_MIN_CONFIDENCE", 0.75))
    if rec.confidence < min_confidence:
        return None

    payload = state.get("input_payload") or {}
    if payload.get("disable_runtime_upgrade"):
        return None

    # Mission upgrade: long-horizon goals or explicit multi-step targets
    if rec.suggested_runtime == "mission":
        goal = str(payload.get("goal") or payload.get("query") or "")
        total_chars = int(payload.get("total_target_chars") or payload.get("mission", {}).get("total_target_chars") or 0)
        if total_chars >= int(getattr(settings, "MISSION_AUTO_MIN_TOTAL_CHARS", 50000)):
            return "mission"
        long_markers = ("长篇", "多章", "mission", "long-form", "multi-step mission")
        if any(m in goal.lower() for m in long_markers):
            return "mission"
        return None

    if rec.suggested_runtime == "supervisor":
        subtasks = payload.get("subtasks") or state.get("subtasks")
        if isinstance(subtasks, list) and len(subtasks) >= 2:
            return "supervisor"
        if payload.get("force_supervisor"):
            return "supervisor"
        return None

    if rec.suggested_runtime == "exploration":
        if payload.get("exploration_enabled") or payload.get("enable_exploration"):
            return "exploration"
        return None

    return None


def apply_pending_runtime_upgrade(state: AgentState) -> AgentState:
    """
    Apply deferred runtime upgrade at turn finalization (not mid-loop).

    Sets execution_mode hint on input_payload for next turn or handoff.
    """
    loop_raw = state.get("react_loop") or {}
    pending = loop_raw.get("pending_runtime_upgrade")
    if not pending:
        rec = loop_raw.get("route_recommendation")
        approved = evaluate_route_recommendation(state, rec)
        if not approved:
            return state
        pending = approved

    payload = dict(state.get("input_payload") or {})
    payload["runtime_upgrade_recommendation"] = {
        "suggested_runtime": pending,
        "source": "react_loop",
        "approved": True,
    }
    payload["enable_planning_mission_handoff"] = pending == "mission"
    if pending == "supervisor":
        payload["execution_mode"] = "supervisor"
    if pending == "exploration":
        payload["execution_mode"] = "exploration"

    state = merge_state(
        state,
        input_payload=payload,
        execution_mode=pending if pending != "mission" else state.get("execution_mode"),
    )
    state = record_react_event(
        state,
        "react_runtime_upgrade_suggested",
        str(pending),
        "runtime_router",
        {"approved": True, "applied_at": "finalize_turn"},
    )
    return state


def store_route_recommendation(state: AgentState, decision_dict: dict[str, Any]) -> AgentState:
    """Persist model route recommendation on react_loop for finalize evaluation."""
    rec_raw = decision_dict.get("route_recommendation")
    if not isinstance(rec_raw, dict):
        return state

    from app.domain.react_loop import get_react_loop, merge_react_loop

    loop = get_react_loop(state)
    loop.route_recommendation = rec_raw
    approved = evaluate_route_recommendation(state, rec_raw)
    if approved:
        loop.pending_runtime_upgrade = approved
    state = merge_react_loop(state, loop)
    state = record_react_event(
        state,
        "react_route_recommended",
        str(rec_raw.get("suggested_runtime") or ""),
        "react_deliberate",
        {
            "recommendation": rec_raw,
            "approved": approved,
        },
    )
    return state
