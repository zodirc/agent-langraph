"""Policy: when intent observation model must run vs structural-only."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.config.settings import settings
from app.runtime.state import AgentState
from app.services.pre_planning import parse_explicit_interaction_mode

_MIXED_QA_ACTION_RE = re.compile(
    r"(?i)(解释|说明|为什么|what|why|how).*(修|改|写|生成|fix|implement|add|create)",
)
_CONTROL_SIGNAL_RE = re.compile(r"(?i)^/(resume|pause|cancel)\b")


@dataclass(frozen=True)
class IntentObservationPolicyDecision:
    invoke_model: bool
    reason: str
    skip_reason: str | None = None


def load_intent_observation_config() -> dict[str, Any]:
    cfg = getattr(settings, "INTENT_OBSERVATION_CONFIG", None)
    return dict(cfg) if isinstance(cfg, dict) else {}


def intent_observation_enabled() -> bool:
    cfg = load_intent_observation_config()
    return bool(cfg.get("enabled", True))


def intent_observation_shadow_mode() -> bool:
    cfg = load_intent_observation_config()
    return bool(cfg.get("shadow_mode", False))


def low_confidence_threshold() -> float:
    cfg = load_intent_observation_config()
    return float(cfg.get("low_confidence_threshold", 0.55))


def should_apply_observation_to_routing(state: AgentState) -> bool:
    """True when observation result may drive mode resolution (not shadow-only)."""
    if not intent_observation_enabled():
        return False
    if intent_observation_shadow_mode():
        return False
    obs = state.get("intent_observation") or {}
    if obs.get("shadow_only"):
        return False
    return True


def decide_intent_observation_policy(
    state: AgentState,
    *,
    explicit_mode: str | None,
    route_audit_seed: dict[str, Any],
) -> IntentObservationPolicyDecision:
    """Return whether L2 model observation is required for this turn."""
    if not intent_observation_enabled():
        return IntentObservationPolicyDecision(
            invoke_model=False,
            reason="disabled",
            skip_reason="intent_observation.disabled",
        )

    payload = state.get("input_payload") or {}
    goal = str(payload.get("goal") or payload.get("query") or "").strip()

    if payload.get("confirm") is True or str(payload.get("confirm_action") or "") == "true":
        return IntentObservationPolicyDecision(
            invoke_model=False,
            reason="structured_confirm",
            skip_reason="confirm:true",
        )

    if _CONTROL_SIGNAL_RE.search(goal):
        return IntentObservationPolicyDecision(
            invoke_model=False,
            reason="api_control_signal",
            skip_reason="control_command",
        )

    from app.services.interaction_goal import goal_is_pure_greeting

    if goal and goal_is_pure_greeting(goal):
        return IntentObservationPolicyDecision(
            invoke_model=False,
            reason="pure_greeting",
            skip_reason="trivial_chat",
        )

    from app.services.interaction_goal import goal_is_capability_inquiry

    if goal and goal_is_capability_inquiry(goal):
        return IntentObservationPolicyDecision(
            invoke_model=False,
            reason="capability_inquiry",
            skip_reason="qa_meta_question",
        )

    confidence = float(route_audit_seed.get("kind_confidence") or 0.0)
    from app.runtime.state_field_access import mission_from_state

    mission_active = bool(mission_from_state(state)) and not payload.get("mission_suspended")
    session_turn = int(state.get("session_turn") or 0)
    if (
        not mission_active
        and session_turn <= 1
        and confidence >= low_confidence_threshold()
    ):
        return IntentObservationPolicyDecision(
            invoke_model=False,
            reason="turn1_structural",
            skip_reason="new_session_high_confidence",
        )

    if payload.get("execution_grant"):
        return IntentObservationPolicyDecision(
            invoke_model=False,
            reason="execution_grant_present",
            skip_reason="mechanical_continue_grant",
        )

    if payload.get("foreground_preempt_consumed") or payload.get("steer_replan_mode") in (
        "rewrite",
        "repair",
    ):
        return IntentObservationPolicyDecision(
            invoke_model=True,
            reason="foreground_preempt_replan",
        )

    explicit_key = explicit_mode or parse_explicit_interaction_mode(payload)
    if explicit_key and explicit_key not in ("auto",):
        from app.services.interaction_goal import explicit_mode_should_apply

        if explicit_mode_should_apply(explicit_key, goal):
            return IntentObservationPolicyDecision(
                invoke_model=False,
                reason="explicit_mode",
                skip_reason=f"explicit={explicit_key}",
            )

    if explicit_key == "auto" or payload.get("interaction_mode") == "auto":
        return IntentObservationPolicyDecision(
            invoke_model=True,
            reason="interaction_mode_auto",
        )

    if mission_active and goal and not payload.get("confirm"):
        return IntentObservationPolicyDecision(
            invoke_model=True,
            reason="mission_active_non_confirm",
        )

    if confidence < low_confidence_threshold():
        return IntentObservationPolicyDecision(
            invoke_model=True,
            reason="low_structural_confidence",
        )

    if payload.get("route_audit_replan") or payload.get("route_audit_replan_feedback"):
        return IntentObservationPolicyDecision(
            invoke_model=True,
            reason="misroute_replan",
        )

    reflection = state.get("reflection_result") or {}
    if reflection.get("retry_planning") or reflection.get("route_audit_on_misroute"):
        return IntentObservationPolicyDecision(
            invoke_model=True,
            reason="reflection_misroute",
        )

    if _MIXED_QA_ACTION_RE.search(goal):
        return IntentObservationPolicyDecision(
            invoke_model=True,
            reason="mixed_qa_and_action",
        )

    current_mode = str(payload.get("current_mode") or payload.get("target_mode") or "").strip()
    if current_mode and confidence >= low_confidence_threshold():
        return IntentObservationPolicyDecision(
            invoke_model=False,
            reason="structural_sufficient",
            skip_reason="stay_session_high_confidence",
        )

    return IntentObservationPolicyDecision(
        invoke_model=True,
        reason="default_ambiguity_guard",
    )


def mechanical_resume_allowed_by_observation(state: AgentState) -> bool:
    """Block mechanical resume when intent observation says semantic steer/replan."""
    obs = state.get("intent_observation") or {}
    if not obs:
        return True
    turn_kind = str(obs.get("turn_kind_candidate") or "")
    if turn_kind in ("steer_replan", "steer_execute", "narrate_only"):
        from app.services.metrics_service import get_metrics_service

        get_metrics_service().inc_mechanical_resume_blocked()
        return False
    if turn_kind == "mechanical_continue":
        return True
    if obs.get("source") == "llm" and float(obs.get("confidence") or 0) >= low_confidence_threshold():
        if turn_kind in ("steer_replan", "steer_execute"):
            return False
    return True
