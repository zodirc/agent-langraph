"""Per-turn writing operator carryover — clear on new user messages, pin only intra-turn."""

from __future__ import annotations

from typing import Any, Mapping

from app.services.writing_batch import reset_batch_turn_counter

PLAYBOOK_OPERATORS = frozenset(
    {
        "append",
        "rewrite",
        "polish",
        "character",
        "replot",
        "kickoff_body",
        "kickoff_novel",
    }
)

OUTLINE_ONLY_OPERATORS = frozenset({"kickoff_novel", "replot"})
BODY_WRITE_OPERATORS = frozenset({"append", "kickoff_body", "rewrite", "polish", "character"})


def same_turn_operator_pin_active(payload: Mapping[str, Any] | dict[str, Any]) -> bool:
    """True when the current turn is mid-replan/batch and may reuse writing_operator."""
    return bool(
        payload.get("force_write_after_reads")
        or payload.get("force_edit_after_reads")
        or str(payload.get("thin_execution_profile") or "") == "writing_batch"
        or payload.get("route_audit_replan")
        or payload.get("execution_grant")
    )


def clear_writing_operator_carryover(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop stale writing control fields when a new substantive user goal arrives."""
    out = reset_batch_turn_counter(payload)
    for key in (
        "writing_operator",
        "thin_execution_profile",
        "force_write_after_reads",
        "force_edit_after_reads",
        "route_audit_replan_feedback",
    ):
        out.pop(key, None)
    return out


def resolve_writing_playbook_operator(
    goal: str,
    state: Mapping[str, Any] | dict[str, Any] | None,
    payload: Mapping[str, Any] | dict[str, Any],
) -> str | None:
    """Classify from goal; only honor payload pin during same-turn replan/batch."""
    from app.services.writing_intent_classifier import classify_writing_operator

    fresh = classify_writing_operator(goal, state)
    pinned = str(payload.get("writing_operator") or "").strip()
    if same_turn_operator_pin_active(payload) and pinned in PLAYBOOK_OPERATORS:
        return pinned
    return fresh
