"""Incremental planning gate router — sole post-planning fork (WP-1.4 / WP-4.2)."""

from __future__ import annotations

from app.config.settings import settings
from app.runtime.router import (
    _effective_selected_tools,
    _failed_route,
    _is_non_retryable_tool_failure,
    _writing_route_allowed,
)
from app.runtime.state import AgentState
from app.services.graph_execution_signals import (
    graph_last_tool_failed,
    graph_turn_had_fatal_error,
)
from app.services.mode_resolution import should_route_engineering_execution
from app.services.retrieval_policy import (
    needs_session_memory_retrieval,
    should_route_to_retrieval_after_planning,
)
from app.services.turn_contract import contract_requires_side_effects, contract_tool_names


def route_after_incremental_planning(state: AgentState) -> str:
    """Post-incremental_planning fork: retrieval / tools / engineering / generation prep."""
    from app.services.planning_retry_signals import (
        apply_planning_replan_signal,
        can_planning_replan_again,
        planning_replan_needed,
    )

    if planning_replan_needed(state) and can_planning_replan_again(state):
        replanned = apply_planning_replan_signal(state)
        from app.services.state_store import get_state_store

        get_state_store().save(replanned)
        return "incremental_planning"

    if graph_turn_had_fatal_error(state):
        return _failed_route(state, "incremental_planning")

    payload = state.get("input_payload") or {}

    from app.services.turn_guard import contract_requires_execution_route

    if contract_requires_execution_route(payload):
        selected_tools = _effective_selected_tools(state)
        if selected_tools:
            return "tool_execution"
        if _writing_route_allowed(state):
            return "context_governance"
        if contract_tool_names(payload):
            return "tool_execution"
        return "tool_execution"

    if (
        graph_last_tool_failed(state)
        and str(state.get("current_node") or "") == "tool_execution"
    ):
        if _is_non_retryable_tool_failure(state):
            return "context_governance"
        if state.get("retry_count", 0) >= settings.MAX_RETRY_COUNT:
            return "dead_letter"

    if should_route_engineering_execution(state):
        return "engineering_execution"

    payload = state.get("input_payload") or {}
    plan = state.get("plan") or []
    selected_tools = _effective_selected_tools(state)
    intent = (payload.get("writing_intent") or {})

    if _writing_route_allowed(state) and not selected_tools and state.get("skip_retrieval"):
        return "context_governance"

    if state.get("skip_retrieval") and not should_route_to_retrieval_after_planning(state):
        if selected_tools:
            return "tool_execution"
        if _writing_route_allowed(state):
            return "context_governance"
        if contract_requires_side_effects(payload, state=state) and contract_tool_names(payload):
            return "tool_execution"
        return "context_governance"

    if needs_session_memory_retrieval(state) and not selected_tools and not intent.get("enabled"):
        return "retrieval"

    needs_retrieval = any(
        "retrieve" in step.lower() or "search" in step.lower() for step in plan
    )
    if intent.get("enabled") and not state.get("skip_retrieval"):
        needs_retrieval = True

    if (
        settings.SKIP_RETRIEVAL_WHEN_NO_TOOLS
        and not selected_tools
        and not needs_retrieval
        and not intent.get("enabled")
    ):
        return "context_governance"

    if needs_retrieval:
        return "retrieval"
    if selected_tools:
        return "tool_execution"
    if _writing_route_allowed(state):
        return "context_governance"
    if contract_requires_side_effects(payload, state=state) and contract_tool_names(payload):
        return "tool_execution"
    return "context_governance"
