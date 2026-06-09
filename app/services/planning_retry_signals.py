"""Planning-stage retry signals (retry_planning moved off post-answer reflection)."""

from __future__ import annotations

from app.runtime.state import AgentState, merge_state
from app.services.route_audit.config import load_route_audit_config


def _route_audit_issues(state: AgentState) -> list[str]:
    payload = state.get("input_payload") or {}
    audit = payload.get("route_audit") or {}
    if audit.get("aligned") is not False:
        return []
    return [str(i) for i in (audit.get("issues") or [])[:5]]


def _turn_contract_issues(state: AgentState) -> list[str]:
    from app.services.turn_contract import validate_turn_contract_execution

    return validate_turn_contract_execution(state)


def planning_replan_needed(state: AgentState) -> bool:
    """True when post-planning route audit is still misaligned."""
    return bool(_route_audit_issues(state))


def execution_contract_replan_needed(state: AgentState) -> bool:
    """Post-tool contract drift that should trigger a planning revision."""
    return bool(_turn_contract_issues(state))


def apply_planning_replan_signal(
    state: AgentState,
    *,
    issues: list[str] | None = None,
) -> AgentState:
    """Flag incremental replan when route audit or execution contract requires it."""
    resolved = list(issues) if issues is not None else _route_audit_issues(state)
    if not resolved:
        return state
    payload = dict(state.get("input_payload") or {})
    if payload.get("route_audit_replan"):
        return state
    from app.services.route_audit.apply import build_replan_feedback

    audit = payload.get("route_audit") or {}
    payload["route_audit_replan"] = True
    payload["route_audit_replan_feedback"] = build_replan_feedback(
        {**audit, "issues": list(dict.fromkeys(list(audit.get("issues") or []) + resolved))}
    )
    revisions = int(state.get("planning_revision_count") or 0) + 1
    return merge_state(
        state,
        input_payload=payload,
        planning_revision_count=revisions,
    )


def can_planning_replan_again(state: AgentState) -> bool:
    cfg = load_route_audit_config()
    revisions = int(state.get("planning_revision_count") or 0)
    return revisions < cfg.max_planning_revisions
