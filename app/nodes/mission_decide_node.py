from __future__ import annotations

import json
from typing import Optional

from app.config.prompts import MISSION_DECIDE_SYSTEM, MISSION_WRITING_DECIDE_SYSTEM
from app.config.settings import settings
from app.domain.mission import StepDecision
from app.domain.packs.registry import get_domain_pack
from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.llm_client import invoke_structured
from app.services.progress_evaluator import evaluate_mission_control
from app.services.state_store import get_state_store


def _rule_step_decision(
    state: AgentState,
    *,
    mission: dict,
    progress: dict,
    observation: dict,
    eval_result,
) -> StepDecision:
    kind = str(mission.get("kind", "single_turn"))
    if eval_result.done and eval_result.action == "finish":
        return StepDecision(action="finish", rationale=eval_result.reason)
    if eval_result.done and eval_result.action == "pause":
        return StepDecision(action="pause", rationale=eval_result.reason)
    if eval_result.action == "escalate":
        return StepDecision(action="escalate", rationale=eval_result.reason)
    try:
        pack = get_domain_pack(kind)
        raw = pack.suggest_step_decision(
            state,
            mission=mission,
            progress=progress,
            observation=observation,
        )
        return StepDecision.from_dict(raw)
    except KeyError:
        return StepDecision(action="continue", next_executor="pipeline:request")


def _llm_step_decision(
    state: AgentState,
    *,
    mission: dict,
    progress: dict,
    observation: dict,
    eval_result,
    system_prompt: Optional[str] = None,
    user_payload: Optional[dict] = None,
) -> StepDecision | None:
    """LLM StepDecision when MISSION_LLM_DECIDE is enabled; None on failure."""
    payload = user_payload or {
        "mission": mission,
        "progress": progress,
        "observation": observation,
        "mission_control_hint": eval_result.to_dict(),
        "mission_step": state.get("mission_step"),
    }
    prompt = system_prompt or MISSION_DECIDE_SYSTEM
    try:
        result = invoke_structured(
            "routing",
            prompt,
            json.dumps(payload, ensure_ascii=False),
        )
        decision = StepDecision.from_dict(result)
        if decision.action not in ("continue", "finish", "pause", "escalate", "retry"):
            return None
        return decision
    except (ValueError, RuntimeError, KeyError):
        return None


def mission_decide_node(state: AgentState) -> AgentState:
    """
    Control: propose next step (pack rules + eval hint).
    When MISSION_LLM_DECIDE=true, LLM proposes StepDecision with rules as fallback.
    """
    from app.services.mission_steer import consume_pending_steer

    state = consume_pending_steer(state)
    from app.services.mission_execution import reconcile_work_plan

    state = reconcile_work_plan(state)
    mission = state.get("mission") or {}
    progress = state.get("progress") or {}
    observation = state.get("observation") or {}

    eval_result = evaluate_mission_control(state)
    from app.services.mission_execution import has_execution_grant
    from app.services.mission_intervention import intervention_from_payload

    payload_for_decide = state.get("input_payload") or {}
    intervention = intervention_from_payload(payload_for_decide) or {}
    forced_pause = bool(
        str(intervention.get("action") or "") == "pause" and intervention.get("force")
    )
    if has_execution_grant(payload_for_decide) and (
        eval_result.done and eval_result.action == "pause"
    ) and not forced_pause:
        from app.services.progress_evaluator import EvalResult

        eval_result = EvalResult(
            done=False,
            reason="execution grant overrides pause",
            action="continue",
        )
    decision: StepDecision
    source = "rules"

    from app.services.writing_phases import (
        build_writing_decide_payload,
        should_use_writing_llm_decide,
        suggest_writing_phase_fallback,
    )

    use_writing_llm = should_use_writing_llm_decide(mission)
    use_llm = bool(getattr(settings, "MISSION_LLM_DECIDE", False)) or use_writing_llm

    if use_llm:
        decide_payload = (
            build_writing_decide_payload(
                state,
                mission=mission,
                progress=progress,
                observation=observation,
                eval_result=eval_result,
            )
            if use_writing_llm
            else None
        )
        llm_decision = _llm_step_decision(
            state,
            mission=mission,
            progress=progress,
            observation=observation,
            eval_result=eval_result,
            system_prompt=MISSION_WRITING_DECIDE_SYSTEM if use_writing_llm else None,
            user_payload=decide_payload,
        )
        if llm_decision is not None:
            if eval_result.done and eval_result.action == "finish":
                decision = StepDecision(action="finish", rationale=eval_result.reason)
                source = "eval_override"
            elif eval_result.done and eval_result.action == "pause":
                decision = StepDecision(action="pause", rationale=eval_result.reason)
                source = "eval_override"
            else:
                decision = llm_decision
                source = "llm"
        else:
            decision = (
                suggest_writing_phase_fallback(state)
                if use_writing_llm
                else _rule_step_decision(
                    state,
                    mission=mission,
                    progress=progress,
                    observation=observation,
                    eval_result=eval_result,
                )
            )
    else:
        decision = (
            suggest_writing_phase_fallback(state)
            if use_writing_llm
            else _rule_step_decision(
                state,
                mission=mission,
                progress=progress,
                observation=observation,
                eval_result=eval_result,
            )
        )

    updated = merge_state(
        state,
        step_decision=decision.to_dict(),
        status=TaskStatus.MISSION_RUNNING.value,
        current_node="mission_decide",
        audit_log=append_audit(
            state,
            "mission_decide",
            "success",
            {
                "action": decision.action,
                "executor": decision.next_executor,
                "source": source,
            },
        ),
    )
    if decision.action == "continue":
        from app.services.mission_execution import consume_execution_grant

        updated = consume_execution_grant(updated)
    get_state_store().save(updated)
    return updated
