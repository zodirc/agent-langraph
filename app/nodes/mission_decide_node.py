"""Mission 决策节点 mission_decide：产出 step_decision。

每轮 consume_pending_steer → reconcile_work_plan → evaluate_mission_control
→ LLM 或规则 → route_after_mission_decide。

mission_decide proposes StepDecision after steer consume and mission control eval.
"""

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
            trace_state=state,
        )
        decision = StepDecision.from_dict(result)
        if decision.action not in ("continue", "finish", "pause", "escalate", "retry"):
            return None
        return decision
    except (ValueError, RuntimeError, KeyError):
        return None


def mission_decide_node(state: AgentState) -> AgentState:
    """控制环决策：pack 规则与 eval 提示，可选 MISSION_LLM_DECIDE。

    Propose StepDecision; LLM when enabled with rules as fallback.
    """
    from app.services.mission_steer import consume_pending_steer

    state = consume_pending_steer(state)
    from app.services.execution_control import (
        CancelRequested,
        PauseRequested,
        check_for_control_signal,
        handle_control_exception,
    )

    try:
        check_for_control_signal(
            str(state["task_id"]),
            phase="mission_decide_enter",
            raise_on_pause=True,
            raise_on_cancel=True,
        )
    except (PauseRequested, CancelRequested) as exc:
        handled = handle_control_exception(state, exc)
        if handled is not None:
            get_state_store().save(handled)
            return handled

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
    from app.services.intent_composer import grant_may_mechanical_forward

    if (
        has_execution_grant(payload_for_decide)
        and grant_may_mechanical_forward(payload_for_decide, state=state)
        and (eval_result.done and eval_result.action == "pause")
        and not forced_pause
    ):
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

    from app.services.mission_oma.orchestrator import (
        mechanical_step_decision,
        should_use_mission_oma,
        stamp_dispatch_context,
    )

    use_oma = should_use_mission_oma(state)
    use_writing_llm = should_use_writing_llm_decide(mission) and not use_oma
    use_llm = (bool(getattr(settings, "MISSION_LLM_DECIDE", False)) or use_writing_llm) and not use_oma
    if use_writing_llm:
        from app.services.legacy_mission_paths import record_legacy_mission_path

        record_legacy_mission_path("writing_llm_decide")

    if use_oma:
        if eval_result.done and eval_result.action == "finish":
            decision = StepDecision(action="finish", rationale=eval_result.reason)
            source = "eval_override"
        elif eval_result.done and eval_result.action == "pause":
            decision = StepDecision(action="pause", rationale=eval_result.reason)
            source = "eval_override"
        else:
            decision = mechanical_step_decision(state)
            source = "oma_mechanical"
        updated = stamp_dispatch_context(
            merge_state(
                state,
                step_decision=decision.to_dict(),
                status=TaskStatus.MISSION_RUNNING.value,
                current_node="mission_decide",
                audit_log=append_audit(
                    state,
                    "mission_decide",
                    "success",
                    {
                        "source": source,
                        "action": decision.action,
                        "next_executor": decision.next_executor,
                        "writing_phase": (decision.params or {}).get("writing_phase"),
                    },
                ),
            )
        )
        get_state_store().save(updated)
        return updated
    elif use_llm:
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
