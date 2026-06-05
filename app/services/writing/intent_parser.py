"""Intent layer — parse planning/API signals into WritingIntentRecord."""

from __future__ import annotations

from typing import Any, Optional

from app.domain.writing_intent_model import IntentAnchor, WritingIntentRecord

_INTENT_ACTIONS = frozenset(
    {
        "rewrite_outline",
        "review_outline",
        "reset_body",
        "edit_plot",
        "write_outline",
        "write_body",
        "run_tools",
        "pause",
        "continue",
        "batch_unit_quality",
    }
)

_COMMAND_INTENT_ACTIONS = frozenset(
    {"rewrite_outline", "review_outline", "reset_body", "edit_plot", "write_outline", "write_body"}
)


def _anchor_from_legacy_edit_spec(block: dict[str, Any]) -> IntentAnchor:
    """Reject legacy edit_spec at parse time — migrate shape only when explicitly present."""
    legacy = dict(block.get("edit_spec") or {})
    if not legacy:
        return IntentAnchor.from_dict(block.get("intent_anchor") or block.get("anchor"))
    return IntentAnchor(
        old_text=str(legacy.get("old_text") or ""),
        new_text=str(legacy.get("new_text") or ""),
        steer_correction=str(legacy.get("steer_correction") or ""),
        target_hint="body" if str(legacy.get("filename") or "").endswith(("novel.txt", ".md")) else "outline",
    )


def parse_intent_from_intervention(block: dict[str, Any], *, source: str = "intervention") -> Optional[WritingIntentRecord]:
    action = str(block.get("action") or "").strip()
    if action not in _INTENT_ACTIONS:
        return None
    anchor = IntentAnchor.from_dict(block.get("intent_anchor") or block.get("anchor"))
    if not any((anchor.old_text, anchor.new_text, anchor.steer_correction, anchor.target_hint)):
        anchor = _anchor_from_legacy_edit_spec(block)
    return WritingIntentRecord(
        action=action,
        force=bool(block.get("force", False)),
        reason=str(block.get("reason") or ""),
        anchor=anchor,
        source=source,
    )


def parse_intent_from_planning(
    planning_result: dict[str, Any],
    payload: Optional[dict[str, Any]] = None,
) -> Optional[WritingIntentRecord]:
    payload = dict(payload or {})
    raw = planning_result.get("mission_intervention")
    if isinstance(raw, dict) and raw.get("action"):
        intent = parse_intent_from_intervention(raw, source="planning_intervention")
        if intent:
            return intent

    wi = planning_result.get("writing_intent") if isinstance(planning_result.get("writing_intent"), dict) else {}
    action = str(wi.get("action") or planning_result.get("writing_action") or "").strip()
    if action in _COMMAND_INTENT_ACTIONS:
        force = bool(payload.get("require_planning_after_steer") or payload.get("steer_applied_at"))
        return WritingIntentRecord(
            action=action,
            force=force,
            reason=str(planning_result.get("steer_intent_summary") or ""),
            anchor=IntentAnchor(),
            source="planning_writing_intent",
        )
    return None


def parse_intent_from_payload(payload: dict[str, Any]) -> Optional[WritingIntentRecord]:
    stored = payload.get("writing_intent_record")
    if isinstance(stored, dict) and stored.get("action"):
        return WritingIntentRecord.from_dict(stored)

    raw = payload.get("mission_intervention")
    if isinstance(raw, dict) and raw.get("action"):
        return parse_intent_from_intervention(raw, source="payload_intervention")
    return None


def store_intent_on_payload(payload: dict[str, Any], intent: WritingIntentRecord) -> dict[str, Any]:
    out = dict(payload)
    out["writing_intent_record"] = intent.to_dict()
    out["mission_intervention"] = {
        "action": intent.action,
        "force": intent.force,
        "reason": intent.reason,
        "intent_anchor": intent.anchor.to_dict(),
    }
    return out
