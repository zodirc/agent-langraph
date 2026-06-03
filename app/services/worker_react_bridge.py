"""Bounded ReAct bridge for OMAW workers — no direct artifact writes."""

from __future__ import annotations

import uuid
from typing import Any

from app.config.settings import settings
from app.domain.react_loop import ReactLoopState
from app.domain.worker_execution_policy import WorkerExecutionPolicy
from app.runtime.state import AgentState, merge_state
from app.services.fact_bundle_builder import attach_fact_bundle_to_state, build_fact_bundle
from app.services.metrics_service import get_metrics_service


def _new_react_session_id(dispatch_id: str) -> str:
    return f"react-{dispatch_id[:12]}-{uuid.uuid4().hex[:6]}"


def run_worker_bounded_react(
    state: AgentState,
    *,
    agent: str,
    capability: str,
    goal: str,
    allowed_actions: list[str],
    max_steps: int,
    policy: WorkerExecutionPolicy | None = None,
) -> AgentState:
    """
    Run bounded ReAct for a worker; rebuild fact_bundle afterward.
    Does not write manuscript artifacts — only enriches state for worker resume.
    """
    from app.domain.worker_execution_policy import default_policy
    from app.domain.react_loop import merge_react_loop
    from app.services.react_loop_runner import (
        execute_bounded_action,
        finalize_loop,
        get_react_loop,
        record_react_observation,
        should_abort_loop,
        should_finish_loop,
    )
    from app.domain.react_loop import ReactDecision
    from app.services.react_loop_runner import llm_decision, rule_based_decision

    pol = policy or default_policy(agent, capability)
    dispatch_id = str((state.get("input_payload") or {}).get("dispatch_id") or state["task_id"])
    react_session_id = _new_react_session_id(dispatch_id)
    metrics = get_metrics_service()
    metrics.inc_worker_react_enter(agent, capability)

    allowed = [a for a in allowed_actions if a]
    if not allowed:
        allowed = list(pol.react.allowed_actions)

    loop = ReactLoopState(
        enabled=True,
        mode="oma_worker",
        goal=goal,
        step_index=0,
        max_steps=max(1, min(max_steps, pol.react.max_steps)),
        status="running",
        allowed_actions=allowed,
    )
    current = merge_react_loop(state, loop)
    payload = dict(current.get("input_payload") or {})
    payload["react_session_id"] = react_session_id
    payload["worker_react"] = {
        "agent": agent,
        "capability": capability,
        "dispatch_id": dispatch_id,
    }
    current = merge_state(current, input_payload=payload)

    def _pick_decision(st: AgentState, lp: ReactLoopState) -> ReactDecision:
        lp.allowed_actions = allowed
        picked = llm_decision(st, lp)
        if picked is None:
            picked = rule_based_decision(st, lp)
        if picked.action not in allowed:
            return ReactDecision(
                thought_summary="action not in worker policy whitelist",
                action="finish",
                action_input={},
                continue_loop=False,
                why="policy_whitelist",
                confidence=0.5,
            )
        return picked

    steps = 0
    while steps < loop.max_steps:
        loop = get_react_loop(current)
        if not loop.enabled:
            break
        decision = _pick_decision(current, loop)
        loop.current_decision = decision.to_dict()
        current = merge_react_loop(current, loop)
        current, observation = execute_bounded_action(current, decision)
        current = record_react_observation(current, decision, observation)
        steps += 1
        loop = get_react_loop(current)
        if should_finish_loop(current, loop, decision, observation):
            current = finalize_loop(current, reason="worker_react_finish", exit_path="finish_with_answer")
            break
        abort, reason = should_abort_loop(current, loop, observation)
        if abort:
            metrics.inc_worker_react_abort(agent, capability, reason)
            current = finalize_loop(
                current,
                reason=reason,
                exit_path="reflection" if reason != "max_replan_exceeded" else "dead_letter",
            )
            break

    ch_raw = (current.get("input_payload") or {}).get("writing_intent") or {}
    chapter_index = ch_raw.get("chapter_index")
    try:
        chapter_index = int(chapter_index) if chapter_index is not None else None
    except (TypeError, ValueError):
        chapter_index = None

    bundle = build_fact_bundle(
        current,
        agent=agent,
        capability=capability,
        chapter_index=chapter_index,
        policy=pol,
    )
    bundle["react_steps"] = steps
    current = attach_fact_bundle_to_state(current, bundle)
    payload = dict(current.get("input_payload") or {})
    payload["react_steps"] = steps
    return merge_state(current, input_payload=payload)


def maybe_worker_react_on_failure(
    state: AgentState,
    *,
    agent: str,
    capability: str,
    reason: str,
) -> AgentState:
    """Enter bounded react when facts insufficient or worker failed."""
    from app.domain.worker_execution_policy import default_policy

    pol = default_policy(agent, capability)
    if not pol.react.enabled or not getattr(settings, "MISSION_OMA_WORKER_REACT_ENABLED", True):
        return state
    goal = f"Recover {capability}: {reason}"
    return run_worker_bounded_react(
        state,
        agent=agent,
        capability=capability,
        goal=goal,
        allowed_actions=list(pol.react.allowed_actions),
        max_steps=pol.react.max_steps,
        policy=pol,
    )
