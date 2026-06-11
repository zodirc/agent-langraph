"""主图条件路由：retrieval / tool / policy 等分支（post-planning 见 planning_gate_router）。"""

from __future__ import annotations

from app.config.settings import settings
from app.runtime.state import AgentState
from app.services.graph_execution_signals import (
    graph_last_tool_failed,
    graph_turn_had_fatal_error,
)
from app.services.mode_execution import mode_blocks_writing
from app.services.route_audit.apply import writing_gate_allowed
from app.services.turn_contract import (
    contract_requires_side_effects,
    contract_tool_names,
)


def _failed_route(state: AgentState, retry_node: str) -> str:
    if state.get("retry_count", 0) >= settings.MAX_RETRY_COUNT:
        return "dead_letter"
    return retry_node


def _writing_route_allowed(state: AgentState) -> bool:
    payload = state.get("input_payload") or {}
    if mode_blocks_writing(payload):
        return False
    intent = payload.get("writing_intent") or {}
    return writing_gate_allowed(state) and bool(intent.get("enabled"))


def _effective_selected_tools(state: AgentState) -> list[str]:
    tools = list(state.get("selected_tools") or [])
    if tools:
        return tools
    return contract_tool_names(state.get("input_payload") or {})


def _is_non_retryable_tool_failure(state: AgentState) -> bool:
    """True when tool_execution recorded a failure that must not be retried."""
    for err in state.get("errors") or []:
        if "tool_execution(non_retryable)" in str(err):
            return True
    log = state.get("audit_log") or []
    if log and str(log[-1].get("action") or "") == "non_retryable_error":
        return True
    for item in state.get("tool_results") or []:
        if item.get("status") not in ("error", "skipped") and not item.get("error"):
            continue
        from app.services.tool_result_helpers import tool_result_flag

        if item.get("non_retryable") or tool_result_flag(item, "non_retryable"):
            return True
        if str(item.get("error_code") or "") == "artifact_not_found":
            return True
    return False


def route_after_retrieval(state: AgentState) -> str:
    """Skip tool_execution when no registered tools were selected."""
    if graph_turn_had_fatal_error(state):
        return _failed_route(state, "retrieval")
    payload = state.get("input_payload") or {}
    if state.get("selected_tools"):
        return "tool_execution"
    if _writing_route_allowed(state):
        return "context_governance"
    if contract_requires_side_effects(payload, state=state) and contract_tool_names(payload):
        return "tool_execution"
    return "context_governance"


def route_after_tool(state: AgentState) -> str:
    """Retry tool node or route to dead letter (architecture §22).

    Convergence judgment comes from the single gate (`evaluate_convergence`);
    legacy guards remain as fallback during the unified-core transition.
    """
    from app.services.converge import (
        NEXT_FINALIZE,
        NEXT_FORCE_WRITE,
        NEXT_REPLAN,
        evaluate_convergence,
    )
    from app.services.planning_retry_signals import (
        apply_force_write_signal,
        apply_planning_replan_signal,
        can_planning_replan_again,
    )

    conv = evaluate_convergence(state)
    if not graph_last_tool_failed(state) and conv.next == NEXT_FORCE_WRITE:
        replanned = apply_force_write_signal(state)
        from app.services.state_store import get_state_store

        get_state_store().save(replanned)
        return "incremental_planning"
    if (
        not graph_last_tool_failed(state)
        and conv.next == NEXT_REPLAN
        and can_planning_replan_again(state)
    ):
        from app.services.turn_contract import validate_turn_contract_execution

        replanned = apply_planning_replan_signal(
            state,
            issues=validate_turn_contract_execution(state) or [f"converge:{conv.reason}"],
        )
        from app.services.state_store import get_state_store

        get_state_store().save(replanned)
        return "incremental_planning"

    if graph_last_tool_failed(state):
        if _is_non_retryable_tool_failure(state):
            return "context_governance"
        if state.get("retry_count", 0) >= settings.MAX_RETRY_COUNT:
            return "dead_letter"
        return "tool_execution"
    if conv.done or conv.next == NEXT_FINALIZE:
        # Single gate says this turn converged (or is stuck): finalize forward,
        # never re-enter the tool loop.
        return "context_governance"
    payload = state.get("input_payload") or {}
    if _writing_route_allowed(state):
        return "context_governance"
    if contract_requires_side_effects(payload, state=state) and contract_tool_names(payload):
        return "tool_execution"
    return "context_governance"


def route_after_engineering(state: AgentState) -> str:
    if graph_turn_had_fatal_error(state):
        return _failed_route(state, "engineering_execution")
    return "policy"


def route_after_policy(state: AgentState) -> str:
    policy_result = state.get("policy_result") or "CONTINUE"
    if policy_result == "CONTINUE":
        return "output"
    if policy_result in ("REVIEW", "ESCALATE"):
        return "human_review"
    return "rejected"


def route_on_error(state: AgentState) -> str:
    if state.get("retry_count", 0) >= settings.MAX_RETRY_COUNT:
        return "dead_letter"
    return state.get("current_node", "incremental_planning")
