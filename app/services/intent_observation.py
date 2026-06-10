"""Unified structured intent observation entry (L2 model + structural merge)."""

from __future__ import annotations

import json
import time
from typing import Any

from app.config.settings import settings
from app.domain.intent_observation import (
    IntentObservationResult,
    new_observation_trace_id,
)
from app.runtime.state import AgentState, append_audit, merge_state
from app.services.intent_observation_policy import (
    decide_intent_observation_policy,
    intent_observation_shadow_mode,
    load_intent_observation_config,
    should_apply_observation_to_routing,
)
from app.services.metrics_service import get_metrics_service
from app.services.mode_router import map_intent_to_mode, normalize_intent_kind

_SYSTEM = """You observe user intent for an agent runtime BEFORE planning executes.

Output ONE JSON object with these fields:
- intent_kind: "qa" | "engineering" | "writing"
- target_mode: "qa_mode" | "engineering_mode" | "manuscript_mode"
- session_relation: "stay" | "switch" | "isolate"
- turn_kind_candidate: "narrate_only" | "execute" | null
- confidence: number 0.0-1.0
- reasons: array of short strings

Rules:
- Do NOT produce execution plans or prose answers.
- execute when the message requires file/tool side effects; narrate_only for pure conversation.
- isolate when user switches to unrelated QA while a long-running task is active.
- Mixed "explain X then fix Y" → engineering or writing based on deliverable."""

_STRUCTURAL_KIND_MAP: dict[str, str] = {
    "manuscript": "writing",
    "writing": "writing",
    "revision": "writing",
    "code": "engineering",
    "interactive_app": "engineering",
    "small_project": "engineering",
    "qa": "qa",
    "general": "qa",
}


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit]


def build_structural_observation(
    state: AgentState,
    *,
    route_audit_seed: dict[str, Any],
    explicit_mode: str | None = None,
) -> IntentObservationResult:
    """L1 structural observation without model call."""
    payload = state.get("input_payload") or {}
    inferred = str(route_audit_seed.get("inferred_kind") or "general")
    confidence = float(route_audit_seed.get("kind_confidence") or 0.0)
    intent_kind = _STRUCTURAL_KIND_MAP.get(inferred.lower(), "qa")

    target_mode = map_intent_to_mode(inferred)
    if intent_kind == "engineering":
        target_mode = "engineering_mode"
    elif intent_kind == "qa":
        target_mode = "qa_mode"

    current_mode = str(payload.get("current_mode") or payload.get("target_mode") or "").strip()
    session_relation = "stay"
    if current_mode and current_mode != target_mode:
        session_relation = "switch"

    reasons = [f"structural_kind={inferred}"]
    if explicit_mode:
        reasons.append(f"explicit_mode={explicit_mode}")

    result = IntentObservationResult(
        source="structural",
        intent_kind=intent_kind,
        target_mode=target_mode,
        session_relation=session_relation,
        turn_kind_candidate=None,
        needs_planning=True,
        confidence=confidence,
        reasons=reasons,
        trace_id=new_observation_trace_id(),
    )
    from app.services.planning_gate_policy import derive_planning_required

    temp_state = {**state, "intent_observation": result.to_dict()}
    planning_required, planning_source = derive_planning_required(temp_state)  # type: ignore[arg-type]
    result.needs_planning = planning_required
    result.reasons = [*result.reasons, f"planning_source={planning_source}"]
    return result


def _invoke_observation_model(
    state: AgentState,
    *,
    route_audit_seed: dict[str, Any],
    explicit_mode: str | None,
) -> IntentObservationResult:
    from app.services.llm_client import invoke_structured
    from app.services.prompt_context_gateway import prepare_governed_payload

    cfg = load_intent_observation_config()
    payload = state.get("input_payload") or {}
    goal = str(payload.get("goal") or payload.get("query") or "").strip()
    trace_id = new_observation_trace_id()
    t0 = time.monotonic()

    base_payload: dict[str, Any] = {
        "user_message": _clip(goal, 4000),
        "route_audit_seed": {
            "inferred_kind": route_audit_seed.get("inferred_kind"),
            "kind_confidence": route_audit_seed.get("kind_confidence"),
        },
        "explicit_mode": explicit_mode,
        "current_mode": payload.get("current_mode") or payload.get("target_mode"),
        "writing_constraints": (payload.get("writing_constraints") or [])[:8],
    }
    user_payload, _ = prepare_governed_payload(
        state, "intent_observation", base_payload
    )

    model_purpose = "intent_observation"
    try:
        raw = invoke_structured(
            model_purpose,
            _SYSTEM,
            json.dumps(user_payload, ensure_ascii=False),
            trace_state=state,
        )
    except Exception as exc:
        structural = build_structural_observation(
            state, route_audit_seed=route_audit_seed, explicit_mode=explicit_mode
        )
        structural.fallback_used = True
        structural.source = "hybrid"
        structural.reasons = [*structural.reasons, f"llm_error={type(exc).__name__}"]
        get_metrics_service().inc_intent_observation_fallback("llm_error")
        return structural

    latency_ms = int((time.monotonic() - t0) * 1000)
    intent_kind = normalize_intent_kind(str(raw.get("intent_kind") or "qa"))
    if intent_kind == "manuscript":
        intent_kind = "writing"
    target_mode = str(raw.get("target_mode") or map_intent_to_mode(intent_kind))
    session_relation = str(raw.get("session_relation") or "stay")
    if session_relation not in ("stay", "switch", "isolate"):
        session_relation = "stay"

    turn_kind = raw.get("turn_kind_candidate")
    turn_kind_str = str(turn_kind) if turn_kind else None
    is_revision = bool(raw.get("is_revision"))
    revision_intent = raw.get("revision_intent")
    if not isinstance(revision_intent, dict):
        revision_intent = None

    result = IntentObservationResult(
        source="llm",
        intent_kind=intent_kind,
        target_mode=target_mode,
        session_relation=session_relation,
        turn_kind_candidate=turn_kind_str,
        needs_planning=True,
        is_revision=is_revision,
        revision_intent=revision_intent,
        confidence=float(raw.get("confidence") or 0.0),
        reasons=[str(r) for r in (raw.get("reasons") or [])][:8],
        trace_id=trace_id,
        model_name=str(cfg.get("primary_model") or "") or None,
        latency_ms=latency_ms,
    )
    from app.services.planning_gate_policy import derive_planning_required

    temp_state = {**state, "intent_observation": result.to_dict()}
    planning_required, planning_source = derive_planning_required(temp_state)  # type: ignore[arg-type]
    result.needs_planning = planning_required
    result.reasons = [*result.reasons, f"planning_source={planning_source}"]
    get_metrics_service().inc_intent_observation(
        source="llm",
        intent_kind=intent_kind,
        session_relation=session_relation,
    )
    return result


def _record_shadow_comparison(
    structural: IntentObservationResult,
    llm_result: IntentObservationResult,
) -> None:
    """Shadow mode: compare structural vs LLM suggestion without applying LLM to routing."""
    disagree = (
        structural.target_mode != llm_result.target_mode
        or structural.intent_kind != llm_result.intent_kind
        or structural.session_relation != llm_result.session_relation
    )
    if disagree:
        get_metrics_service().inc_stay_switch_isolate_disagreement(
            structural.session_relation,
            llm_result.session_relation,
        )
    if structural.target_mode != llm_result.target_mode:
        get_metrics_service().inc_mode_resolution_misroute("shadow_target_mode")


def observe_intent(
    state: AgentState,
    *,
    explicit_mode: str | None,
    route_audit_seed: dict[str, Any],
) -> IntentObservationResult:
    """Run L0–L2 observation; model only when policy requires."""
    from app.services.intent_snapshot import get_or_freeze_intent_snapshot
    from app.services.pre_planning import parse_explicit_interaction_mode

    cached = get_or_freeze_intent_snapshot(state)
    if cached is not None:
        return cached

    from app.services.intent_snapshot import current_intent_snapshot

    snap = current_intent_snapshot(state)
    payload = dict(state.get("input_payload") or {})
    if snap and snap.snapshot_status == "frozen":
        payload["intent_recompute_count"] = int(payload.get("intent_recompute_count") or 0) + 1
        payload["intent_recompute_reason"] = "observe_intent_rerun"
        state = merge_state(state, input_payload=payload)

    explicit = explicit_mode or parse_explicit_interaction_mode(state.get("input_payload") or {})
    policy = decide_intent_observation_policy(
        state, explicit_mode=explicit, route_audit_seed=route_audit_seed
    )
    structural = build_structural_observation(
        state, route_audit_seed=route_audit_seed, explicit_mode=explicit
    )

    if intent_observation_shadow_mode() and policy.invoke_model:
        llm_result = _invoke_observation_model(
            state, route_audit_seed=route_audit_seed, explicit_mode=explicit
        )
        _record_shadow_comparison(structural, llm_result)
        structural.shadow_only = True
        structural.reasons = [
            *structural.reasons,
            "shadow_mode=active",
            f"shadow_llm_target={llm_result.target_mode}",
        ]
        return structural

    if not policy.invoke_model:
        structural.reasons = [*structural.reasons, f"policy_skip={policy.skip_reason or policy.reason}"]
        get_metrics_service().inc_intent_observation(
            source="structural",
            intent_kind=structural.intent_kind,
            session_relation=structural.session_relation,
        )
        return structural

    llm_result = _invoke_observation_model(
        state, route_audit_seed=route_audit_seed, explicit_mode=explicit
    )
    if explicit and explicit != "auto":
        llm_result = _respect_explicit_mode(llm_result, explicit, structural)
    return llm_result


def _respect_explicit_mode(
    result: IntentObservationResult,
    explicit: str,
    structural: IntentObservationResult,
) -> IntentObservationResult:
    """Explicit user mode cannot be overridden except for session_relation hints."""
    from app.services.pre_planning import _EXPLICIT_MODE_MAP

    if explicit not in _EXPLICIT_MODE_MAP:
        return result
    kind_override, mode_override = _EXPLICIT_MODE_MAP[explicit]
    if kind_override == "manuscript":
        resolved_kind = "writing"
    elif kind_override:
        resolved_kind = normalize_intent_kind(kind_override)
    else:
        resolved_kind = result.intent_kind or structural.intent_kind
    merged_reasons = [*result.reasons, f"explicit_mode_preserved={explicit}"]
    return IntentObservationResult(
        version=result.version,
        source="hybrid" if result.source == "llm" else result.source,
        intent_kind=resolved_kind,
        target_mode=mode_override,
        session_relation=result.session_relation,
        turn_kind_candidate=result.turn_kind_candidate,
        needs_planning=result.needs_planning,
        is_revision=result.is_revision,
        revision_intent=result.revision_intent,
        confidence=max(result.confidence, 0.9),
        reasons=merged_reasons,
        trace_id=result.trace_id,
        model_name=result.model_name,
        latency_ms=result.latency_ms,
        fallback_used=result.fallback_used,
    )


def apply_intent_observation_to_state(
    state: AgentState,
    result: IntentObservationResult,
) -> AgentState:
    """Write observation to state, payload audit, and turn metrics."""
    from app.services.intent_snapshot import (
        current_intent_snapshot,
        freeze_intent_snapshot,
        get_or_freeze_intent_snapshot,
    )
    from app.services.planning_gate_policy import derive_planning_required

    shadow = intent_observation_shadow_mode()
    result_dict = result.to_dict()
    if shadow:
        result_dict["shadow_only"] = True

    cached = get_or_freeze_intent_snapshot(state)
    snapshot_hit = cached is not None and cached.intent_kind == result.intent_kind

    payload = dict(state.get("input_payload") or {})
    if snapshot_hit:
        payload["intent_snapshot_hit"] = True
        payload["intent_snapshot_source"] = "frozen"
    else:
        planning_required, planning_source = derive_planning_required(
            {**state, "intent_observation": result_dict}  # type: ignore[arg-type]
        )
        result.needs_planning = planning_required
        result_dict["needs_planning"] = planning_required
        state = freeze_intent_snapshot(
            state,
            result,
            planning_required=planning_required,
            planning_required_source=planning_source,
        )
        payload = dict(state.get("input_payload") or payload)
        payload["planning_required_source"] = planning_source
    audit = dict(payload.get("route_audit") or {})
    audit["pre_observation"] = True
    audit["intent_observation_summary"] = {
        "source": result.source,
        "intent_kind": result.intent_kind,
        "target_mode": result.target_mode,
        "session_relation": result.session_relation,
        "needs_planning": result.needs_planning,
        "is_revision": result.is_revision,
        "confidence": result.confidence,
        "trace_id": result.trace_id,
        "shadow_only": shadow,
    }
    if result.revision_intent:
        audit["revision_scope"] = result.revision_intent.get("revision_scope")
        audit["target_sections"] = result.revision_intent.get("target_sections")
    payload["route_audit"] = audit

    from app.services.turn_event_log import record_turn_event

    audit_entry = {
        "observation_source": result.source,
        "confidence": result.confidence,
        "explicit_mode_present": bool(payload.get("interaction_mode")),
        "session_relation": result.session_relation,
        "needs_planning": result.needs_planning,
        "is_revision": result.is_revision,
        "planning_required_source": payload.get("planning_required_source") or "rule",
        "intent_snapshot_hit": payload.get("intent_snapshot_hit"),
        "model_name": result.model_name,
        "latency_ms": result.latency_ms,
        "fallback_used": result.fallback_used,
        "shadow_only": shadow,
        "trace_id": result.trace_id,
    }
    state = merge_state(
        state,
        intent_observation=result_dict,
        input_payload=payload,
        audit_log=append_audit(state, "intent_observation", "observed", audit_entry),
    )
    return record_turn_event(
        state,
        "intent_observed",
        "pre_planning",
        "intent_observation",
        result_dict,
    )


def resolve_mode_with_observation(state: AgentState) -> bool:
    """Whether mode resolution should consume intent_observation."""
    return should_apply_observation_to_routing(state)
