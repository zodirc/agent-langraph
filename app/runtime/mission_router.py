from __future__ import annotations

from app.config.settings import settings
from app.runtime.state import AgentState, TaskStatus


def route_after_mission_decide(state: AgentState) -> str:
    decision = state.get("step_decision") or {}
    action = str(decision.get("action", "continue"))
    if action == "finish":
        return "finalize"
    if action == "pause":
        return "finalize"
    if action == "escalate":
        return "policy"
    return "mission_act"


def route_after_mission_eval(state: AgentState) -> str:
    control = state.get("mission_control") or {}
    if control.get("done"):
        return "finalize"
    step = int(state.get("mission_step") or 0)
    budget = (state.get("mission") or {}).get("budget") or {}
    raw_max = budget.get("max_steps")
    max_steps = (
        int(raw_max)
        if raw_max is not None
        else int(getattr(settings, "MISSION_MAX_STEPS", 500))
    )
    if step >= max_steps:
        return "finalize"
    if str(state.get("status", "")) == TaskStatus.FAILED.value:
        return "dead_letter"
    return "mission_decide"


def route_mission_finalize(state: AgentState) -> str:
    """After mission loop: policy → output for user-facing answer."""
    if state.get("reasoning_result"):
        return "policy"
    return "mission_decide"
