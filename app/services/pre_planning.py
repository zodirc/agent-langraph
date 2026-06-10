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
    from app.services.revision_side_effects import maybe_invalidate_intent_on_steer_confirm

    state = maybe_invalidate_intent_on_steer_confirm(state)
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


def planning_must_run_llm(state: AgentState) -> bool:
    """True when steer, replan, or reflection forces a full planning LLM call."""
    payload = state.get("input_payload") or {}
    if payload.get("route_audit_replan") or payload.get("route_audit_replan_feedback"):
        return True
    if payload.get("plan_validation_feedback"):
        return True
    reflection = state.get("reflection_result") or {}
    if reflection.get("retry_planning"):
        return True
    from app.services.mission_steer import steer_requires_planning

    if steer_requires_planning(payload):
        return True
    return int(state.get("planning_revision_count") or 0) > 0


def should_skip_qa_planning_llm(state: AgentState) -> bool:
    """Thin planning for conversational QA (greetings, short chat) in qa_mode."""
    payload = state.get("input_payload") or {}
    if not payload.get("pre_planning_completed"):
        return False
    if str(payload.get("target_mode") or "") != "qa_mode":
        return False
    if planning_must_run_llm(state):
        return False
    from app.services.interaction_goal import goal_is_conversational_qa

    goal = str(payload.get("goal") or payload.get("query") or "").strip()
    return goal_is_conversational_qa(goal)


def should_skip_edit_plot_planning_llm(state: AgentState) -> bool:
    """Fast path: known edit_plot/review_outline steer with existing artifact → skip planning LLM."""
    payload = state.get("input_payload") or {}
    from app.services.mission_steer import planning_steer_replan_active

    if not planning_steer_replan_active(payload, state):
        return False
    from app.services.artifact_resolver import outline_exists
    from app.services.edit_scope import classify_edit_action, steer_implies_global_rewrite

    steer = str(payload.get("latest_steer_message") or payload.get("goal") or "").strip()
    if not steer or not outline_exists(state):
        return False
    if steer_implies_global_rewrite(steer):
        return False
    intervention = payload.get("mission_intervention") or {}
    action = str(intervention.get("action") or "")
    if action in ("edit_plot", "review_outline"):
        return True
    if classify_edit_action(steer, outline_exists=True) == "edit_plot":
        return True
    return False


def should_skip_revision_planning_llm(state: AgentState) -> bool:
    """Revision fast path: executable revision intent → skip planning LLM."""
    payload = state.get("input_payload") or {}
    if not payload.get("pre_planning_completed"):
        return False
    intent_obs = state.get("intent_observation") or {}
    if not intent_obs.get("is_revision"):
        return False
    from app.services.writing.revision_command import revision_intent_executable

    revision_intent = intent_obs.get("revision_intent") or payload.get("revision_intent")
    if not revision_intent_executable(revision_intent):
        return False
    if planning_must_run_llm(state):
        return False
    return True


def should_skip_planning_llm(state: AgentState) -> bool:
    """
    Thin planning for engineering delivery (like Cursor Agent on a repo task).

    Skip full planning LLM when mode is already engineering and this is not a
    steer/replan/retry turn. Uses rule-derived planning_required from intent observation.
    """
    payload = state.get("input_payload") or {}
    if not payload.get("pre_planning_completed"):
        return False

    from app.services.planning_gate_policy import derive_planning_required

    planning_required, _source = derive_planning_required(state)
    if not planning_required:
        if str(payload.get("target_mode") or "") == "engineering_mode":
            return True
        return False

    if str(payload.get("target_mode") or "") != "engineering_mode":
        return False
    from app.services.interaction_goal import goal_is_conversational_qa

    goal = str(payload.get("goal") or payload.get("query") or "").strip()
    if goal_is_conversational_qa(goal):
        return False
    if planning_must_run_llm(state):
        return False
    if should_skip_edit_plot_planning_llm(state):
        return True
    return True


def qa_thin_plan(goal: str, *, intent_kind: str = "qa") -> list[str]:
    """Thin plan step from intent + goal shape (no length-only heuristics)."""
    from app.services.interaction_goal import goal_is_pure_greeting

    text = (goal or "").strip()
    if goal_is_pure_greeting(text):
        return ["respond greeting"]
    return ["respond directly"]


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


def revision_thin_plan() -> list[str]:
    return [
        "revision_thin: scoped read → edit_text_artifact",
        "contract: edit_plot",
    ]


def revision_thin_tools() -> list[str]:
    return ["read_text_artifact", "edit_text_artifact"]


def revision_thin_tool_stages() -> list[list[str]]:
    return [["read_text_artifact"], ["edit_text_artifact"]]
