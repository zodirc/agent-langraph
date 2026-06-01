"""SRDL execute node — run one bounded action from whitelist."""

from __future__ import annotations

from app.domain.react_loop import ReactDecision, get_react_loop
from app.runtime.state import AgentState, append_audit, merge_state
from app.services.react_loop_runner import execute_bounded_action
from app.services.state_store import get_state_store


def react_execute_node(state: AgentState) -> AgentState:
    """
    Execute the action selected by react_deliberate.

    Reads: react_loop.current_decision
    Writes: retrieved_knowledge, tool_results, reasoning_result, turn_facts
    """
    loop = get_react_loop(state)
    raw = loop.current_decision or {}
    decision = ReactDecision.from_dict(raw)

    updated, observation = execute_bounded_action(state, decision)
    updated = merge_state(
        updated,
        current_node="react_execute",
        audit_log=append_audit(
            updated,
            "react_execute",
            str(decision.action),
            observation,
        ),
    )
    # Stash last observation for observe node
    loop = get_react_loop(updated)
    loop.current_decision = {**decision.to_dict(), "_last_observation": observation}
    from app.domain.react_loop import merge_react_loop

    updated = merge_react_loop(updated, loop)
    get_state_store().save(updated)
    return updated
