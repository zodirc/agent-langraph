"""SRDL deliberate node — gap analysis and bounded action selection."""

from __future__ import annotations

from app.domain.react_loop import ReactDecision, get_react_loop
from app.runtime.state import AgentState, append_audit, merge_state
from app.services.react_loop_runner import deliberate_next_action
from app.services.runtime_router import store_route_recommendation
from app.services.state_store import get_state_store


def react_deliberate_node(state: AgentState) -> AgentState:
    """
    Deliberate next bounded action (not user-facing reasoning).

    Reads: react_loop, turn_facts, plan, selected_tools
    Writes: react_loop.current_decision, audit_log
    """
    state, decision = deliberate_next_action(state)
    if decision.route_recommendation:
        state = store_route_recommendation(state, decision.to_dict())

    loop = get_react_loop(state)
    if decision.action not in (loop.allowed_actions or []):
        from app.services.react_audit import record_react_event

        state = record_react_event(
            state,
            "react_action_blocked",
            decision.action,
            "react_deliberate",
            {"reason": "action_not_in_whitelist", "allowed": loop.allowed_actions},
        )

    updated = merge_state(
        state,
        current_node="react_deliberate",
        audit_log=append_audit(
            state,
            "react_deliberate",
            "decision",
            {
                "action": decision.action,
                "step_index": loop.step_index,
                "thought_summary": decision.thought_summary[:120],
            },
        ),
    )
    get_state_store().save(updated)
    return updated
