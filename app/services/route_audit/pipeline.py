"""路由审计管线
planning_node 结束后调用；misroute 可触发 reflection 或 planning 修订。

Post-planning route audit: compare plan vs signals → corrections or replan flag."""

from __future__ import annotations

from app.runtime.state import AgentState
from app.services.route_audit.apply import apply_route_corrections
from app.services.route_audit.audit import audit_planned_route
from app.services.route_audit.config import load_route_audit_config


def run_route_audit_pipeline(state: AgentState) -> AgentState:
    cfg = load_route_audit_config()
    if not cfg.enabled:
        return state
    audit = audit_planned_route(state, cfg=cfg)
    payload = dict(state.get("input_payload") or {})
    payload["route_audit"] = audit
    state = merge_state_with_payload(state, payload)
    state = apply_route_corrections(state, audit)
    from app.services.reasoning_shortcut import normalize_reasoning_policy
    from app.services.reasoning_trace import (
        report_effective_plan_trace,
        report_route_audit_trace,
    )

    report_route_audit_trace(state)
    report_effective_plan_trace(state)
    state = normalize_reasoning_policy(state)
    from app.services.turn_event_log import record_turn_event

    aligned = bool((state.get("input_payload") or {}).get("route_audit", {}).get("aligned", True))
    state = record_turn_event(
        state,
        "route_audited" if aligned else "route_misaligned",
        "planning",
        "route_audit",
        {"aligned": aligned, "issues": (state.get("input_payload") or {}).get("route_audit", {}).get("issues")},
    )
    if not audit.get("aligned"):
        post = audit_planned_route(state, cfg=cfg)
        post["prior_issues"] = audit.get("issues")
        post["corrected"] = True
        payload = dict(state.get("input_payload") or {})
        payload["route_audit"] = post
        state = merge_state_with_payload(state, payload)
        report_route_audit_trace(state)
        report_effective_plan_trace(state)
        state = normalize_reasoning_policy(state)
    return state


def merge_state_with_payload(state: AgentState, payload: dict) -> AgentState:
    from app.runtime.state import merge_state

    return merge_state(state, input_payload=payload)
