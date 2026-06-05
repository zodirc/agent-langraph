"""Command Builder — compile WritingIntentRecord + manuscript into WritingCommand."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from app.config.settings import settings
from app.domain.mission import StepPolicy
from app.domain.writing_command import WritingCommand
from app.domain.writing_intent_model import IntentAnchor, WritingIntentRecord
from app.services.outline_steer_patch import is_outline_filename
from app.services.writing.intent_parser import parse_intent_from_payload

COMMAND_ACTIONS = frozenset(
    {
        "edit_plot",
        "review_outline",
        "reset_body",
        "write_outline",
        "write_body",
    }
)


def _normalize_action(action: str) -> str:
    if action == "rewrite_outline":
        return "write_outline"
    return action


def _default_outline_name(manuscript: dict[str, Any], policy: StepPolicy) -> str:
    return str(
        manuscript.get("outline_path")
        or policy.outline_artifact
        or getattr(settings, "MANUSCRIPT_DEFAULT_OUTLINE", "outline.txt")
    )


def _default_body_name(manuscript: dict[str, Any], policy: StepPolicy) -> str:
    return str(
        manuscript.get("body_path")
        or policy.body_artifact
        or getattr(settings, "MANUSCRIPT_DEFAULT_BODY", "novel.txt")
    )


def _resolve_confirmation_status(payload: dict[str, Any]) -> str:
    if payload.get("steer_intent_confirmed"):
        return "approved"
    if payload.get("steer_intent_pending_confirm"):
        return "pending"
    if payload.get("steer_applied_at") and not payload.get("steer_intent_confirmed"):
        return "pending"
    return "approved"


def _resolve_command_id(payload: dict[str, Any], item: Optional[dict[str, Any]]) -> str:
    item_params = dict((item or {}).get("params") or {})
    if item_params.get("command_id"):
        return str(item_params["command_id"])
    existing = payload.get("writing_command")
    if isinstance(existing, dict) and existing.get("command_id"):
        return str(existing["command_id"])
    item_id = str((item or {}).get("id") or "")
    if item_id:
        return f"cmd-{item_id}"
    return uuid.uuid4().hex[:12]


def _target_for_intent(
    action: str,
    *,
    manuscript: dict[str, Any],
    policy: StepPolicy,
    anchor: IntentAnchor,
) -> tuple[str, str]:
    hint = str(anchor.target_hint or "").strip().lower()
    if hint == "body":
        return "body", _default_body_name(manuscript, policy)
    if hint == "outline":
        return "outline", _default_outline_name(manuscript, policy)

    if action in ("edit_plot", "review_outline", "write_outline"):
        return "outline", _default_outline_name(manuscript, policy)
    if action in ("reset_body", "write_body"):
        return "body", _default_body_name(manuscript, policy)
    return "outline", _default_outline_name(manuscript, policy)


def _edit_spec_from_anchor(anchor: IntentAnchor, target_filename: str) -> dict[str, Any]:
    spec: dict[str, Any] = {"filename": target_filename}
    if anchor.old_text:
        spec["old_text"] = anchor.old_text
    if anchor.new_text:
        spec["new_text"] = anchor.new_text
    if anchor.steer_correction:
        spec["steer_correction"] = anchor.steer_correction
    return spec


def _resolve_intent(
    state: dict[str, Any],
    item: Optional[dict[str, Any]],
    *,
    intent: Optional[WritingIntentRecord] = None,
    action: Optional[str] = None,
) -> WritingIntentRecord:
    if intent is not None:
        return intent
    payload = dict(state.get("input_payload") or {})
    parsed = parse_intent_from_payload(payload)
    if parsed:
        return parsed
    if action:
        return WritingIntentRecord(action=_normalize_action(action), source="explicit_action")
    if item and item.get("kind"):
        return WritingIntentRecord(action=_normalize_action(str(item["kind"])), source="work_item")
    return WritingIntentRecord(action="edit_plot", source="default")


def build_writing_command(
    state: dict[str, Any],
    item: Optional[dict[str, Any]] = None,
    *,
    intent: Optional[WritingIntentRecord] = None,
    action: Optional[str] = None,
) -> WritingCommand:
    """Single resolver: intent + manuscript metadata → WritingCommand."""
    payload = dict(state.get("input_payload") or {})
    mission = state.get("mission") or {}
    manuscript = dict(state.get("manuscript") or {})
    policy = StepPolicy.from_dict(mission.get("step_policy") or {})

    item_params = dict((item or {}).get("params") or {})
    bound = payload.get("writing_command")
    if (
        isinstance(bound, dict)
        and bound.get("action")
        and item_params.get("command_id")
        and str(bound.get("command_id")) == str(item_params["command_id"])
    ):
        return WritingCommand.from_dict(bound)

    record = _resolve_intent(state, item, intent=intent, action=action)
    resolved_action = _normalize_action(record.action)
    if resolved_action not in COMMAND_ACTIONS:
        resolved_action = _normalize_action(str((item or {}).get("kind") or resolved_action))

    target_kind, target_filename = _target_for_intent(
        resolved_action,
        manuscript=manuscript,
        policy=policy,
        anchor=record.anchor,
    )

    requires_confirmation = resolved_action in (
        "edit_plot",
        "review_outline",
        "reset_body",
    ) and bool(payload.get("steer_applied_at") or payload.get("steer_watch_outcome"))

    write_spec: dict[str, Any] = {}
    if resolved_action in ("write_outline", "write_body", "reset_body"):
        write_spec = {
            "target_chars": policy.chars_per_step,
            "outline_max_chars": policy.outline_max_chars,
            "require_read_first": resolved_action != "write_body",
        }

    edit_spec = (
        _edit_spec_from_anchor(record.anchor, target_filename)
        if resolved_action == "edit_plot"
        else {}
    )

    return WritingCommand(
        command_id=_resolve_command_id(payload, item),
        action=resolved_action,
        target_kind=target_kind,  # type: ignore[arg-type]
        target_filename=target_filename,
        edit_spec=edit_spec,
        write_spec=write_spec,
        requires_confirmation=requires_confirmation,
        confirmation_status=_resolve_confirmation_status(payload),  # type: ignore[arg-type]
        origin_turn=str(payload.get("steer_applied_at") or ""),
        retry_policy="no_auto_retry",
    )


def build_from_intent(
    state: dict[str, Any],
    intent: WritingIntentRecord,
    *,
    item: Optional[dict[str, Any]] = None,
) -> WritingCommand:
    payload = store_intent_payload(dict(state.get("input_payload") or {}), intent)
    return build_writing_command({**state, "input_payload": payload}, item, intent=intent)


def store_intent_payload(payload: dict[str, Any], intent: WritingIntentRecord) -> dict[str, Any]:
    from app.services.writing.intent_parser import store_intent_on_payload

    return store_intent_on_payload(payload, intent)
