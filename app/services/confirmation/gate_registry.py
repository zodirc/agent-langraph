"""确认门
intent_gate_required: steer 后规划有实质变更时需用户确认再 mission_act。

Declarative confirmation gates (config confirmation_gates).
Rules in config; this module evaluates GateContext only (no side effects)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.services.confirmation.config import load_confirmation_gates_config
from app.services.mission_intervention import intervention_from_payload


@dataclass
class GateContext:
    planning_result: dict[str, Any]
    payload: dict[str, Any]
    state: Optional[dict[str, Any]] = None
    completed_item: Optional[dict[str, Any]] = None
    mission_before: Optional[dict[str, Any]] = None
    observation: Optional[dict[str, Any]] = None


def _intervention(ctx: GateContext) -> dict[str, Any]:
    intervention = intervention_from_payload(ctx.payload) or {}
    raw = ctx.planning_result.get("mission_intervention")
    if isinstance(raw, dict) and raw.get("action"):
        intervention = raw
    if ctx.completed_item and not intervention:
        intervention = intervention_from_payload(ctx.payload) or {}
    return intervention


def intent_gate_required(ctx: GateContext) -> bool:
    """True when steer planning produced a material change worth user OK before act."""
    cfg = load_confirmation_gates_config()
    if not cfg.enabled:
        return False

    payload = ctx.payload
    if payload.get("steer_intent_confirmed"):
        return False
    if not payload.get("steer_applied_at"):
        return False

    intervention = _intervention(ctx)
    action = str((intervention or {}).get("action") or "")
    if action in cfg.material_intervention_actions:
        return True
    if intervention and intervention.get("force"):
        return True

    mission_after = payload.get("mission") if isinstance(payload.get("mission"), dict) else {}
    before = ctx.mission_before or {}
    try:
        after_total = int(
            mission_after.get("total_target_chars")
            or (mission_after.get("success_criteria") or {}).get("target")
            or 0
        )
        before_total = int(
            before.get("total_target_chars")
            or (before.get("success_criteria") or {}).get("target")
            or 0
        )
    except (TypeError, ValueError):
        after_total = before_total = 0
    if after_total > 0 and after_total != before_total:
        return True

    if ctx.planning_result.get("steer_intent_summary"):
        return True

    return False


def _steer_watch_outcome(payload: dict[str, Any], cfg) -> bool:
    if payload.get("steer_watch_outcome"):
        return True
    if payload.get("require_planning_after_steer"):
        return True
    if payload.get("steer_applied_at"):
        return True
    if payload.get("writing_stopped_for_steer"):
        return True
    intervention = intervention_from_payload(payload) or {}
    return str(intervention.get("action") or "") in cfg.intervention_watch_actions


def outcome_gate_required(ctx: GateContext) -> bool:
    """True when a material work item finished under steer watch and needs acceptance."""
    cfg = load_confirmation_gates_config()
    if not cfg.enabled:
        return False

    payload = ctx.payload
    if payload.get("steer_outcome_pending_confirm"):
        return False
    if payload.get("steer_intent_pending_confirm"):
        return False

    observation = ctx.observation or {}
    if observation.get("has_failures"):
        return False

    if not _steer_watch_outcome(payload, cfg):
        return False

    item = ctx.completed_item or {}
    kind = str(item.get("kind") or "")
    if kind not in cfg.outcome_work_item_kinds:
        return False

    # append interrupted by steer — only gate when delta exists
    if kind in ("append_body", "append_chapter") and payload.get("writing_stopped_for_steer"):
        progress = (ctx.state or {}).get("progress") or {}
        delta = progress.get("writing_step_delta") or {}
        if not str(delta.get("excerpt") or "").strip():
            return False

    work_item_id = str(item.get("id") or "")
    if _outcome_already_confirmed(payload, work_item_id):
        return False

    return True


def _outcome_already_confirmed(payload: dict[str, Any], work_item_id: str) -> bool:
    confirmed = payload.get("steer_outcome_confirmed_for")
    if confirmed and str(confirmed) == str(work_item_id):
        return True
    batch = payload.get("steer_applied_at")
    batches = payload.get("steer_outcome_confirmed_batches") or []
    if batch and batch in batches and not work_item_id:
        return True
    return False
