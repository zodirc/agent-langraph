"""Intent adapter — planning/API intervention → WritingIntentRecord → WritingCommand."""

from __future__ import annotations

from typing import Any, Literal, Optional

from app.config.settings import settings
from app.domain.writing_intent_model import IntentAnchor, WritingIntentRecord

InterventionAction = Literal[
    "rewrite_outline",
    "review_outline",
    "reset_body",
    "edit_plot",
    "run_tools",
    "pause",
    "continue",
    "enqueue_work",
    "batch_unit_quality",
]

_ALLOWED_ACTIONS = frozenset(
    {
        "rewrite_outline",
        "review_outline",
        "reset_body",
        "edit_plot",
        "run_tools",
        "pause",
        "continue",
        "enqueue_work",
        "batch_unit_quality",
    }
)

_WRITING_COMMAND_ACTIONS = frozenset(
    {"rewrite_outline", "review_outline", "reset_body", "edit_plot"}
)


def normalize_payload_execution_fields(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    if not isinstance(out.get("tool_params"), dict):
        out["tool_params"] = {}
    wi = out.get("writing_intent")
    if wi is not None and not isinstance(wi, dict):
        out.pop("writing_intent", None)
    if out.get("mission") is not None and not isinstance(out.get("mission"), dict):
        out.pop("mission", None)
    return out


def reconcile_payload_mission(
    payload: dict[str, Any],
    *,
    state_mission: Any = None,
    state_input_payload: dict[str, Any] | None = None,
    preserved_mission: Any = None,
) -> dict[str, Any]:
    """Prefer dict mission blocks; drop string scalars that break ``.get`` chains."""
    out = dict(payload)
    raw = out.get("mission")
    if isinstance(raw, dict):
        return out
    for candidate in (
        preserved_mission,
        state_mission,
        (state_input_payload or {}).get("mission"),
    ):
        if isinstance(candidate, dict) and candidate:
            out["mission"] = candidate
            return out
    out.pop("mission", None)
    return out


def intervention_from_payload(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    from app.services.writing.intent_parser import parse_intent_from_payload

    intent = parse_intent_from_payload(payload)
    if not intent:
        return None
    return {
        "action": intent.action,
        "force": intent.force,
        "reason": intent.reason,
        "intent_anchor": intent.anchor.to_dict(),
        "work_item": None,
    }


def _normalize_intervention(block: dict[str, Any]) -> Optional[WritingIntentRecord]:
    from app.services.writing.intent_parser import parse_intent_from_intervention

    return parse_intent_from_intervention(block, source="intervention")


def is_forced(intervention: Optional[dict[str, Any]]) -> bool:
    return bool(intervention and intervention.get("force"))


def intervention_to_writing_intent(
    intervention: dict[str, Any],
    *,
    mission_step: int,
) -> dict[str, Any]:
    from app.services.writing.intent_parser import parse_intent_from_intervention
    from app.services.writing.command_builder import build_from_intent
    from app.services.writing.command_intent import command_to_writing_intent

    intent = parse_intent_from_intervention(intervention, source="forced_intervention")
    if not intent:
        return {"enabled": False, "action": "continue", "mission_step": mission_step}
    command = build_from_intent({"input_payload": {}}, intent)
    out = command_to_writing_intent(command, mission_step=mission_step)
    out["forced"] = intent.force
    out["source"] = "forced_intervention"
    return out


def coerce_steer_intervention(
    state: dict[str, Any],
    intent: WritingIntentRecord,
) -> WritingIntentRecord:
    if intent.action not in ("rewrite_outline", "write_outline", "edit_plot"):
        return intent

    from app.services.manuscript_service import resolve_manuscript

    task_id = str(state.get("task_id") or "")
    stored = state.get("manuscript") or {}
    ms = resolve_manuscript(task_id, stored) if task_id else None
    anchor = intent.anchor

    if anchor.old_text and anchor.new_text:
        return WritingIntentRecord(
            action="edit_plot",
            force=intent.force,
            reason=intent.reason,
            anchor=IntentAnchor(
                old_text=anchor.old_text,
                new_text=anchor.new_text,
                target_hint="outline",
            ),
            source=intent.source,
        )

    from app.services.artifact_resolver import outline_exists
    from app.services.edit_scope import classify_edit_action

    payload = state.get("input_payload") or {}
    steer = str(payload.get("latest_steer_message") or payload.get("goal") or "")
    action = classify_edit_action(steer, outline_exists=outline_exists(state))
    if action == "rewrite_outline":
        return WritingIntentRecord(
            action="rewrite_outline",
            force=intent.force,
            reason=intent.reason or steer[:240],
            anchor=IntentAnchor(steer_correction=steer[-2000:] if steer else "", target_hint="outline"),
            source=intent.source,
        )
    if not outline_exists(state):
        return intent
    return WritingIntentRecord(
        action="edit_plot",
        force=intent.force,
        reason=intent.reason or "outline edit per steer",
        anchor=IntentAnchor(
            steer_correction=steer[-2000:] if steer else "",
            target_hint="outline",
        ),
        source=intent.source,
    )


def apply_planning_intervention(
    planning_result: dict[str, Any],
    payload: dict[str, Any],
    *,
    state: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    from app.services.writing.intent_parser import parse_intent_from_planning

    intent = parse_intent_from_planning(planning_result, payload)
    if not intent:
        raw = planning_result.get("mission_intervention")
        if isinstance(raw, dict) and raw.get("action"):
            intent = _normalize_intervention(raw)
    if not intent:
        return normalize_payload_execution_fields(payload)
    if state is not None:
        intent = coerce_steer_intervention(state, intent)
    return apply_intent_to_payload(payload, intent)


def apply_intent_to_payload(
    payload: dict[str, Any],
    intent: WritingIntentRecord,
) -> dict[str, Any]:
    from app.services.writing.intent_parser import store_intent_on_payload
    from app.services.writing.command_builder import build_from_intent
    from app.services.writing.state_machine import enqueue_command

    out = store_intent_on_payload(normalize_payload_execution_fields(payload), intent)
    action = intent.action

    if action in ("rewrite_outline", "reset_body", "edit_plot"):
        out["steer_watch_outcome"] = True

    if action == "review_outline":
        from app.services.mission_steer import apply_review_outline_mode

        mission = out.get("mission") if isinstance(out.get("mission"), dict) else {}
        return apply_review_outline_mode(out, mission, intent=intent)

    if action in _WRITING_COMMAND_ACTIONS:
        command = build_from_intent({"input_payload": out}, intent)
        out = enqueue_command(out, command)

    if action == "run_tools":
        pass

    if intent.force and action not in ("edit_plot", "review_outline"):
        out["force_slow_reasoning"] = True
        out.pop("skip_planning_llm", None)
    elif action in ("edit_plot", "review_outline") and intent.force:
        out.pop("force_slow_reasoning", None)

    from app.services.turn_contract import apply_turn_contract_to_payload, build_turn_contract

    contract = build_turn_contract(
        {"mission_intervention": out.get("mission_intervention")},
        out,
        steer_planning_turn=bool(out.get("require_planning_after_steer")),
    )
    out = apply_turn_contract_to_payload(out, contract)
    return out


def apply_intervention_to_payload(
    payload: dict[str, Any],
    intervention: dict[str, Any],
) -> dict[str, Any]:
    intent = _normalize_intervention(intervention)
    if not intent:
        return normalize_payload_execution_fields(payload)
    return apply_intent_to_payload(payload, intent)
