"""
Entry gate for Self-Routed Deliberation Loop (SRDL).

Classifies requests into:
  1. Fixed flow — continue existing explicit graph routing
  2. Bounded ReAct — enter SRDL sub-loop
  3. Higher runtime — mission / supervisor / exploration (unchanged)
"""

from __future__ import annotations

from app.config.settings import settings
from app.runtime.state import AgentState
from app.services.mission_schema import should_use_mission_runtime
from app.services.retrieval_policy import needs_session_memory_retrieval
from app.services.turn_contract import contract_blocks_writing, contract_tool_names


def _writing_route_allowed(state: AgentState) -> bool:
    from app.services.route_audit.apply import writing_gate_allowed

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


def _needs_runtime_discovery(state: AgentState) -> bool:
    """Heuristic: task benefits from dynamic retrieve/tool/reason switching."""
    payload = state.get("input_payload") or {}
    if payload.get("force_react_loop"):
        return True
    if payload.get("disable_react_loop"):
        return False

    plan = state.get("plan") or []
    selected_tools = _effective_selected_tools(state)
    needs_retrieval = any(
        "retrieve" in step.lower() or "search" in step.lower() for step in plan
    )
    has_tools = bool(selected_tools)
    needs_memory = needs_session_memory_retrieval(state)

    # Complex QA: tools + retrieval, or ambiguous multi-step plan
    if has_tools and (needs_retrieval or needs_memory):
        return True
    if len(plan) >= 3 and (has_tools or needs_retrieval):
        return True
    if payload.get("react_loop_hint") == "complex_qa":
        return True

    # Information gap signals
    query = str(payload.get("query") or payload.get("goal") or "")
    gap_markers = ("查找", "检索", "搜索", "分析", "对比", "计算", "verify", "search", "analyze")
    if any(m in query.lower() for m in gap_markers) and (has_tools or needs_retrieval):
        return True

    return False


def should_enter_react_loop(state: AgentState) -> bool:
    """Return True when request should use bounded SRDL instead of fixed routing."""
    payload = state.get("input_payload") or {}
    if payload.get("disable_react_loop"):
        return False

    exec_mode = str(state.get("execution_mode") or "single").lower()
    if should_use_mission_runtime(payload, exec_mode):
        return False
    if _writing_route_allowed(state):
        return False
    if contract_blocks_writing(payload):
        return False

    forced = bool(payload.get("force_react_loop"))
    if forced:
        return True

    if not getattr(settings, "REACT_LOOP_ENABLED", False):
        return False

    return _needs_runtime_discovery(state)


def init_react_loop_state(state: AgentState) -> dict:
    """Build initial react_loop dict for a new SRDL session."""
    from app.domain.react_loop import ReactLoopState

    payload = state.get("input_payload") or {}
    goal = str(payload.get("query") or payload.get("goal") or "")
    max_steps = int(getattr(settings, "REACT_LOOP_MAX_STEPS", 4))
    if payload.get("react_loop_complex"):
        max_steps = int(getattr(settings, "REACT_LOOP_MAX_STEPS_COMPLEX", 6))

    allowed = list(getattr(settings, "REACT_LOOP_ALLOWED_ACTIONS", []))
    if not allowed:
        allowed = ["retrieve_knowledge", "call_tool", "reason", "finish"]
        if getattr(settings, "REACT_LOOP_REPLAN_ENABLED", True):
            allowed.extend(["retrieve_memory", "replan"])

    loop = ReactLoopState(
        enabled=True,
        mode="bounded",
        goal=goal,
        step_index=0,
        max_steps=max_steps,
        status="running",
        allowed_actions=allowed,
    )
    return loop.to_dict()
