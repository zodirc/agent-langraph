"""ReAct 子图路由
主图 planning 在 should_enter_react_loop 时进入 react_deliberate。

SRDL subgraph routing (react_deliberate → execute → observe → finalize)."""

from __future__ import annotations

from app.config.settings import settings
from app.domain.react_loop import get_react_loop
from app.runtime.state import AgentState, TaskStatus
from app.services.react_loop_runner import should_abort_loop, should_finish_loop


def route_after_react_deliberate(state: AgentState) -> str:
    loop = get_react_loop(state)
    decision_raw = loop.current_decision or {}
    action = str(decision_raw.get("action") or "finish")

    if action not in (loop.allowed_actions or []):
        state_exit = str(decision_raw.get("exit_path") or "reflection")
        return "reflection" if state_exit == "reflection" else "reasoning"

    if action == "finish":
        return "react_finalize"
    return "react_execute"


def route_after_react_execute(state: AgentState) -> str:
    if str(state.get("status")) == TaskStatus.FAILED.value:
        if state.get("retry_count", 0) >= settings.MAX_RETRY_COUNT:
            return "dead_letter"
    return "react_observe"


def route_after_react_observe(state: AgentState) -> str:
    from app.domain.react_loop import ReactDecision

    loop = get_react_loop(state)
    last = loop.history[-1] if loop.history else None
    if not last:
        return "react_finalize"

    decision = ReactDecision(
        thought_summary=last.thought_summary,
        action=last.action,
        action_input=last.action_input,
        continue_loop=last.continue_loop,
        why=last.why,
        confidence=last.confidence,
    )
    observation = {"action": last.action, "status": "ok"}
    if "失败" in last.observation_summary or "failed" in last.observation_summary:
        observation["status"] = "failed"

    abort, abort_reason = should_abort_loop(state, loop, observation)
    if abort:
        if abort_reason == "max_replan_exceeded":
            return "planning"
        if abort_reason == "action_blocked":
            return "reflection"
        if state.get("retry_count", 0) >= settings.MAX_RETRY_COUNT:
            return "dead_letter"
        return "reflection"

    if should_finish_loop(state, loop, decision, observation):
        return "react_finalize"

    if loop.step_index >= loop.max_steps:
        return "react_finalize"

    return "react_deliberate"


def route_after_react_finalize(state: AgentState) -> str:
    loop = get_react_loop(state)
    exit_path = str(loop.exit_path or "finish_with_answer")

    if exit_path == "back_to_planning":
        return "planning"
    if exit_path == "reflection":
        return "reflection"
    if exit_path == "human_review":
        return "human_review"
    if exit_path == "dead_letter":
        return "dead_letter"
    return "reasoning"
