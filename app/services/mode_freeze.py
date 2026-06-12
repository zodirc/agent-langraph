"""Per-turn mode freeze when high-confidence intent aligns with structural route audit."""

from __future__ import annotations

from typing import Any

from app.runtime.state import AgentState
from app.services.intent_observation_policy import (
    low_confidence_threshold,
    structural_aligns_with_explicit_mode,
)
from app.services.intent_snapshot import current_intent_snapshot
from app.services.pre_planning import parse_explicit_interaction_mode


def _mode_replan_allowed(state: AgentState) -> bool:
    payload = state.get("input_payload") or {}
    if payload.get("route_audit_replan") or payload.get("route_audit_replan_feedback"):
        return True
    reflection = state.get("reflection_result") or {}
    if reflection.get("retry_planning"):
        return True
    if payload.get("require_planning_after_steer") and not payload.get("steer_planning_done"):
        return True
    return False


def intent_route_audit_aligned(state: AgentState | dict[str, Any]) -> bool:
    """True when frozen intent target_mode matches structural route audit."""
    obs = state.get("intent_observation") or {}
    target = str(obs.get("target_mode") or "")
    if not target:
        return False
    payload = state.get("input_payload") or {}
    audit = payload.get("route_audit") or {}
    explicit = parse_explicit_interaction_mode(payload)
    if explicit and structural_aligns_with_explicit_mode(
        explicit,
        {
            "inferred_kind": audit.get("inferred_kind"),
            "kind_confidence": audit.get("kind_confidence"),
        },
    ):
        return target == str((obs.get("target_mode") or ""))
    inferred = str(audit.get("inferred_kind") or "").lower()
    if inferred in ("manuscript", "writing", "revision"):
        expected = "manuscript_mode"
    elif inferred in ("code", "interactive_app", "small_project"):
        expected = "engineering_mode"
    elif inferred in ("qa", "general"):
        expected = "qa_mode"
    else:
        from app.services.mode_router import map_intent_to_mode

        expected = map_intent_to_mode(inferred)
    return target == expected


def should_freeze_mode_resolution(state: AgentState) -> bool:
    """Freeze mode for this turn when intent is high-confidence and structurally aligned."""
    if _mode_replan_allowed(state):
        return False
    obs = state.get("intent_observation") or {}
    confidence = float(obs.get("confidence") or 0.0)
    if confidence < low_confidence_threshold():
        return False
    snap = current_intent_snapshot(state)
    if snap is None or snap.snapshot_status != "frozen":
        return False
    if not intent_route_audit_aligned(state):
        return False
    return bool(str(obs.get("target_mode") or snap.target_mode))


def record_planning_mode_entry(state: AgentState, *, phase: str) -> None:
    """Track mode at planning entry to detect within-turn oscillation."""
    payload = state.get("input_payload") or {}
    current = str(payload.get("target_mode") or "")
    prior = str(payload.get("_mode_at_planning_enter") or "")
    if prior and current and prior != current:
        from app.services.metrics_service import get_metrics_service

        get_metrics_service().inc_mode_oscillation(prior, current, phase=phase)
    if current:
        payload["_mode_at_planning_enter"] = current
