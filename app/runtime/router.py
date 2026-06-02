from __future__ import annotations

from app.config.settings import settings
from app.runtime.state import AgentState, TaskStatus
from app.services.mission_schema import should_use_mission_runtime
from app.services.retrieval_policy import (
    needs_session_memory_retrieval,
    should_route_to_retrieval_after_planning,
)
from app.services.route_audit.apply import writing_gate_allowed
from app.services.react_entry import should_enter_react_loop
from app.services.turn_contract import contract_blocks_writing, contract_tool_names


def _failed_route(state: AgentState, retry_node: str) -> str:
    if state.get("retry_count", 0) >= settings.MAX_RETRY_COUNT:
        return "dead_letter"
    return retry_node


def _writing_route_allowed(state: AgentState) -> bool:
    payload = state.get("input_payload") or {}
    if contract_blocks_writing(payload):
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
        if item.get("non_retryable") or (item.get("result") or {}).get("non_retryable"):
            return True
        if str(item.get("error_code") or "") == "artifact_not_found":
            return True
    return False


def route_after_retrieval(state: AgentState) -> str:
    """Skip tool_execution when no registered tools were selected."""
    if str(state.get("status", "")) == TaskStatus.FAILED.value:
        return _failed_route(state, "retrieval")
    if state.get("selected_tools"):
        return "tool_execution"
    if _writing_route_allowed(state):
        return "writing"
    return "reasoning"


def route_after_tool(state: AgentState) -> str:
    """Retry tool node or route to dead letter (architecture §22)."""
    status = str(state.get("status", ""))
    if status == TaskStatus.TOOL_FAILED.value:
        # Non-retryable failures (e.g. missing artifact) must not loop tool_execution.
        if _is_non_retryable_tool_failure(state):
            return "reasoning"
        if state.get("retry_count", 0) >= settings.MAX_RETRY_COUNT:
            return "dead_letter"
        return "tool_execution"
    if _writing_route_allowed(state):
        return "writing"
    return "reasoning"


def route_after_writing(state: AgentState) -> str:
    status = str(state.get("status", ""))
    if status == TaskStatus.WRITING_FAILED.value:
        if state.get("retry_count", 0) >= settings.MAX_RETRY_COUNT:
            return "dead_letter"
        return "writing"
    return "reasoning"


def route_after_planning(state: AgentState) -> str:
    status = str(state.get("status", ""))
    if status == TaskStatus.FAILED.value:
        return _failed_route(state, "planning")

    # If the graph is re-invoked from a snapshot where the last node failed
    # (e.g. tool execution exhausted retries), don't silently restart the normal
    # planning route and mark the task as completed.
    if (
        status == TaskStatus.TOOL_FAILED.value
        and str(state.get("current_node") or "") == "tool_execution"
    ):
        if _is_non_retryable_tool_failure(state):
            return "reasoning"
        if state.get("retry_count", 0) >= settings.MAX_RETRY_COUNT:
            return "dead_letter"

    payload = state.get("input_payload") or {}
    # Main graph stops at planning and hands off to mission_graph (mission_step < 1).
    # Inside mission_act's inline pipeline, mission_step is already bumped — continue.
    if should_use_mission_runtime(payload, str(state.get("execution_mode") or "")):
        if int(state.get("mission_step") or 0) < 1:
            return "end"

    if should_enter_react_loop(state):
        return "react_deliberate"

    plan = state.get("plan") or []
    selected_tools = _effective_selected_tools(state)
    intent = (state.get("input_payload") or {}).get("writing_intent") or {}

    if (
        _writing_route_allowed(state)
        and not selected_tools
        and state.get("skip_retrieval")
    ):
        return "writing"

    if state.get("skip_retrieval") and not should_route_to_retrieval_after_planning(state):
        if selected_tools:
            return "tool_execution"
        if _writing_route_allowed(state):
            return "writing"
        return "reasoning"
    if (
        needs_session_memory_retrieval(state)
        and not selected_tools
        and not intent.get("enabled")
    ):
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
        if _writing_route_allowed(state):
            return "writing"
        return "reasoning"

    needs_tools = len(selected_tools) > 0

    if needs_retrieval:
        return "retrieval"
    if needs_tools:
        return "tool_execution"
    if _writing_route_allowed(state):
        return "writing"
    return "reasoning"


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


def route_after_reasoning(state: AgentState) -> str:
    status = str(state.get("status", ""))
    if status in (TaskStatus.FAILED.value, TaskStatus.REASON_FAILED.value):
        if state.get("retry_count", 0) >= settings.MAX_RETRY_COUNT:
            return "dead_letter"
        return "reasoning"
    if should_reflect(state):
        return "reflection"
    return "policy"


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
            return "planning"
    if retry_reasoning:
        return "reasoning"
    return "policy"


def route_after_policy(state: AgentState) -> str:
    policy_result = state.get("policy_result") or "CONTINUE"

    if policy_result == "CONTINUE":
        return "output"
    if policy_result in ("REVIEW", "ESCALATE"):
        return "human_review"
    return "rejected"


def route_after_output_guard(state: AgentState) -> str:
    guard = state.get("output_guard_result") or {}
    if guard.get("passed") is False:
        return "rejected"
    return "output"


def route_after_policy_to_guard(state: AgentState) -> str:
    """Policy routing with output_guard inserted before output."""
    policy_result = state.get("policy_result") or "CONTINUE"
    if policy_result == "CONTINUE":
        if getattr(settings, "OUTPUT_GUARD_ENABLED", True):
            return "output_guard"
        return "output"
    if policy_result in ("REVIEW", "ESCALATE"):
        return "human_review"
    return "rejected"


def route_on_error(state: AgentState) -> str:
    if state.get("retry_count", 0) >= settings.MAX_RETRY_COUNT:
        return "dead_letter"
    return state.get("current_node", "planning")
