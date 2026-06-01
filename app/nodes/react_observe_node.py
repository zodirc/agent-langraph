"""SRDL observe node — record structured observation and update loop history."""

from __future__ import annotations

from app.domain.react_loop import ReactDecision, get_react_loop
from app.runtime.state import AgentState, append_audit, merge_state
from app.services.react_loop_runner import record_react_observation
from app.services.state_store import get_state_store


def react_observe_node(state: AgentState) -> AgentState:
    """
    Convert action result into structured observation and append to loop history.

    Reads: react_loop.current_decision._last_observation
    Writes: react_loop.history, turn_facts, turn_event_log
    """
    loop = get_react_loop(state)
    raw = loop.current_decision or {}
    observation = dict(raw.get("_last_observation") or {"action": raw.get("action"), "status": "ok"})
    decision = ReactDecision.from_dict(raw)

    updated = record_react_observation(state, decision, observation)
    updated = merge_state(
        updated,
        current_node="react_observe",
        audit_log=append_audit(
            updated,
            "react_observe",
            "recorded",
            {"step": loop.step_index, "action": decision.action},
        ),
    )
    get_state_store().save(updated)
    return updated
