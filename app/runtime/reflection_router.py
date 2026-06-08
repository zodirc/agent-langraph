"""Reflection routing helpers (merged into verification on main spine)."""

from __future__ import annotations

from app.config.settings import settings
from app.runtime.state import AgentState
from app.services.mission_schema import should_use_mission_runtime


def should_reflect(state: AgentState) -> bool:
    """Whether to run reflection before policy (Ch4)."""
    if not getattr(settings, "REFLECTION_ENABLED", True):
        return False
    payload = state.get("input_payload") or {}
    if should_use_mission_runtime(payload, str(state.get("execution_mode") or "")):
        return False
    if payload.get("reflection_enabled") is False:
        return False
    max_rounds = int(getattr(settings, "REFLECTION_MAX_ROUNDS", 2))
    if int(state.get("reflection_count") or 0) >= max_rounds:
        return False
    if payload.get("reflection_enabled") is True:
        return True
    reasoning = state.get("reasoning_result") or {}
    structured = reasoning.get("structured") or {}
    if structured.get("fact_warnings"):
        return True
    if float(reasoning.get("confidence", 1.0)) < 0.6:
        return True
    audit = payload.get("route_audit") or {}
    if getattr(settings, "REFLECTION_ROUTE_AUDIT_ON_MISROUTE", True) and audit.get("aligned") is False:
        return True
    if getattr(settings, "REFLECTION_WRITING_ONLY", True):
        intent = payload.get("writing_intent") or {}
        return bool(intent.get("enabled"))
    return True


def route_after_reflection(state: AgentState) -> str:
    """After critique: retry planning/reasoning or proceed to policy."""
    from app.services.route_audit.config import load_route_audit_config

    reflection = state.get("reflection_result") or {}
    verdict = reflection.get("verdict") or {}
    recommended = str(verdict.get("recommended_action") or "")
    max_rounds = int(getattr(settings, "REFLECTION_MAX_ROUNDS", 2))
    if int(state.get("reflection_count") or 0) >= max_rounds:
        return "policy"

    retry_planning = recommended == "replan" or bool(reflection.get("retry_planning"))
    retry_reasoning = recommended == "retry_same_step" or bool(reflection.get("retry_reasoning"))

    if retry_planning:
        cfg = load_route_audit_config()
        revisions = int(state.get("planning_revision_count") or 0)
        if revisions < cfg.max_planning_revisions:
            return "incremental_planning"
    if retry_reasoning:
        return "reasoning"
    return "policy"
