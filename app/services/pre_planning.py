"""
Pre-planning: resolve intent and interaction mode before the planning LLM.

Mainstream pattern (Cursor/Copilot): pick interaction mode first, then plan within
that mode — not full planning then retroactive mode correction.
"""

from __future__ import annotations

from typing import Any, Literal

from app.runtime.state import AgentState, append_audit, merge_state
from app.services.mode_resolution import (
    apply_mode_isolation_if_needed,
    apply_mode_contract_to_state,
)
from app.services.mode_router import (
    ModeResolution,
    normalize_intent_kind,
    resolve_target_mode,
)
from app.services.route_audit.config import load_route_audit_config
from app.services.route_audit.inference import infer_task_kind

InteractionMode = Literal["chat", "engineering", "writing"]

_ENGINEERING_KINDS = frozenset({"code", "interactive_app", "small_project"})
_EXPLICIT_MODE_MAP: dict[str, tuple[str | None, str]] = {
    "chat": (None, "qa_mode"),
    "qa": (None, "qa_mode"),
    "ask": (None, "qa_mode"),
    "engineering": (None, "engineering_mode"),
    "code": (None, "engineering_mode"),
    "deliver": (None, "engineering_mode"),
    "writing": ("manuscript", "manuscript_mode"),
    "manuscript": ("manuscript", "manuscript_mode"),
}


def parse_explicit_interaction_mode(payload: dict[str, Any]) -> InteractionMode | None:
    raw = (
        payload.get("interaction_mode")
        or payload.get("delivery_mode")
        or payload.get("interaction")
    )
    if raw is None:
        return None
    key = str(raw).strip().lower()
    if key in _EXPLICIT_MODE_MAP:
        return key  # type: ignore[return-value]
    return None


def _apply_explicit_mode_override(
    resolution: ModeResolution,
    payload: dict[str, Any],
) -> ModeResolution:
    """User/API selected interaction mode overrides inferred target_mode."""
    explicit = parse_explicit_interaction_mode(payload)
    if not explicit:
        return resolution
    goal = str(payload.get("goal") or payload.get("query") or "").strip()
    from app.services.interaction_goal import explicit_mode_should_apply

    if not explicit_mode_should_apply(explicit, goal):
        return resolution
    kind_override, mode_override = _EXPLICIT_MODE_MAP[explicit]
    intent_kind = (
        normalize_intent_kind(kind_override)
        if kind_override
        else resolution.intent_kind
    )
    if intent_kind in _ENGINEERING_KINDS:
        mode_override = "engineering_mode"
    reason = f"{resolution.mode_switch_reason};explicit_interaction_mode={explicit}"
    contract = resolution.contract
    from app.services.mode_registry import get_mode_contract

    from app.services.mode_router import _resolve_switch_action

    contract = get_mode_contract(mode_override) or contract
    action, action_reason = _resolve_switch_action(
        current_mode=resolution.current_mode,
        target_mode=mode_override,
        contract=contract,
    )
    return ModeResolution(
        intent_kind=intent_kind,
        target_mode=mode_override,
        confidence=max(resolution.confidence, 0.9),
        current_mode=resolution.current_mode,
        mode_switch_action=action,
        mode_switch_reason=f"{action_reason};{reason}",
        contract=contract,
    )


def seed_pre_planning_route_audit(state: AgentState) -> dict[str, Any]:
    """Infer kind from goal + structural signals (no planner output required)."""
    cfg = load_route_audit_config()
    inferred = infer_task_kind(state, cfg=cfg)
    primary = str(inferred.get("primary_kind") or "general")
    confidence = float(inferred.get("confidence") or 0.0)
    return {
        "enabled": cfg.enabled,
        "inferred_kind": primary,
        "kind_scores": dict(inferred.get("kind_scores") or {}),
        "kind_confidence": confidence,
        "pre_planning": True,
        "structural": inferred.get("structural"),
    }


def run_pre_planning_pipeline(state: AgentState) -> AgentState:
    """
    Early intent → mode resolution (before planning LLM).

    Pipeline: explicit mode → structural route audit → intent observation → mode contract.
    Writes: intent_observation, route_audit (partial), mode_resolution, target_mode.
    """
    from app.services.intent_observation import (
        apply_intent_observation_to_state,
        observe_intent,
        resolve_mode_with_observation,
    )

    payload = dict(state.get("input_payload") or {})
    explicit = parse_explicit_interaction_mode(payload)
    audit_seed = seed_pre_planning_route_audit(state)
    audit_seed["phase"] = "pre_observation"
    payload["route_audit"] = {**dict(payload.get("route_audit") or {}), **audit_seed}
    state = merge_state(state, input_payload=payload)

    observation = observe_intent(
        state,
        explicit_mode=explicit,
        route_audit_seed=audit_seed,
    )
    state = apply_intent_observation_to_state(state, observation)

    intent_obs = state.get("intent_observation")
    resolution = resolve_target_mode(
        state,
        intent_observation=intent_obs if resolve_mode_with_observation(state) else None,
    )
    resolution = _apply_explicit_mode_override(resolution, payload)

    state = apply_mode_isolation_if_needed(state, resolution)
    state = apply_mode_contract_to_state(state, resolution)

    from app.services.reasoning_trace import report_mode_resolution_trace

    report_mode_resolution_trace(state)

    payload_out = dict(state.get("input_payload") or {})
    payload_out["pre_planning_completed"] = True
    state = merge_state(
        state,
        input_payload=payload_out,
        audit_log=append_audit(
            state,
            "pre_planning",
            "mode_resolved",
            {
                "target_mode": resolution.target_mode,
                "intent_kind": resolution.intent_kind,
                "explicit": parse_explicit_interaction_mode(payload_out),
                "intent_observation_trace": (state.get("intent_observation") or {}).get(
                    "trace_id"
                ),
            },
        ),
    )
    return state


def should_skip_planning_llm(state: AgentState) -> bool:
    """
    Thin planning for engineering delivery (like Cursor Agent on a repo task).

    Skip full planning LLM when mode is already engineering and this is not a
    steer/replan/retry turn. Also respects intent_observation.needs_planning.
    """
    payload = state.get("input_payload") or {}
    if not payload.get("pre_planning_completed"):
        return False

    intent_obs = state.get("intent_observation") or {}
    if intent_obs.get("needs_planning") is False:
        if str(payload.get("target_mode") or "") == "engineering_mode":
            return True
        return False
    if intent_obs.get("needs_planning") is True:
        pass  # fall through to engineering checks

    if str(payload.get("target_mode") or "") != "engineering_mode":
        return False
    from app.services.interaction_goal import goal_is_conversational_qa

    goal = str(payload.get("goal") or payload.get("query") or "").strip()
    if goal_is_conversational_qa(goal):
        return False
    if payload.get("route_audit_replan") or payload.get("route_audit_replan_feedback"):
        return False
    if payload.get("plan_validation_feedback"):
        return False
    reflection = state.get("reflection_result") or {}
    if reflection.get("retry_planning"):
        return False
    from app.services.mission_steer import steer_requires_planning

    if steer_requires_planning(payload):
        return False
    if int(state.get("planning_revision_count") or 0) > 0:
        return False
    return True


def engineering_thin_plan() -> list[str]:
    return [
        "pre_planning: interaction_mode=engineering",
        "engineering_bounded: generate files, write session, run verify_backend",
    ]


def engineering_thin_tools(state: AgentState) -> list[str]:
    payload = state.get("input_payload") or {}
    contract = payload.get("effective_mode_contract") or {}
    tools = contract.get("allowed_tools")
    if isinstance(tools, list) and tools:
        return [str(t) for t in tools]
    return ["mkdir_path", "read_file", "verify_backend"]
