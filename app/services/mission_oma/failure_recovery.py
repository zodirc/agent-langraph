"""OMAW failure cascade: worker → react → rebuild bundle → narrow replan → dead letter (ADR 10.6)."""

from __future__ import annotations

from app.config.settings import settings
from app.runtime.state import AgentState, TaskStatus, merge_state
from app.services.fact_bundle_builder import attach_fact_bundle_to_state, build_fact_bundle
from app.services.worker_react_bridge import run_worker_bounded_react


def handle_worker_failure(
    state: AgentState,
    *,
    agent: str,
    capability: str,
    reason: str,
    chapter_index: int | None = None,
) -> AgentState:
    """
    Apply ADR 10.6 recovery order until success or dead_letter.
    """
    from app.domain.worker_execution_policy import default_policy
    from app.services.mission_oma.orchestrator import narrow_replan_after_acceptance_fail

    pol = default_policy(agent, capability)
    payload = dict(state.get("input_payload") or {})
    attempts = int(payload.get("oma_recovery_attempts") or 0)
    max_attempts = 3

    if attempts >= max_attempts:
        return merge_state(
            state,
            status=TaskStatus.FAILED.value,
            input_payload={
                **payload,
                "oma_exit_path": "dead_letter",
                "oma_failure_reason": reason,
            },
            errors=list(state.get("errors") or []) + [f"OMAW dead_letter: {reason}"],
        )

    payload["oma_recovery_attempts"] = attempts + 1
    state = merge_state(state, input_payload=payload)

    if getattr(settings, "MISSION_OMA_WORKER_REACT_ENABLED", True) and pol.react.enabled:
        state = run_worker_bounded_react(
            state,
            agent=agent,
            capability=capability,
            goal=f"Recover: {reason}",
            allowed_actions=list(pol.react.allowed_actions),
            max_steps=pol.react.max_steps,
            policy=pol,
        )
        if str(state.get("status", "")) != TaskStatus.FAILED.value:
            return state

    bundle = build_fact_bundle(
        state,
        agent=agent,
        capability=capability,
        chapter_index=chapter_index,
        policy=pol,
    )
    state = attach_fact_bundle_to_state(state, bundle)
    payload = dict(state.get("input_payload") or {})
    if bundle.get("fact_bundle_id"):
        return state

    if payload.get("intent_spec") or payload.get("acceptance_replan"):
        return narrow_replan_after_acceptance_fail(state)

    return merge_state(
        state,
        status=TaskStatus.FAILED.value,
        input_payload={**payload, "oma_exit_path": "human_review"},
        errors=list(state.get("errors") or []) + [reason],
    )
