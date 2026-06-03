"""Kept for import compatibility only; no NLP

Deprecated — use app.services.mission_intervention for explicit forced intervention.
regex routing."""

from __future__ import annotations

from typing import Any, Optional

from app.services.mission_intervention import (
    InterventionAction,
    intervention_from_payload,
    is_forced,
)

RevisionIntent = InterventionAction


def revision_from_payload(payload: dict[str, Any]) -> Optional[str]:
    block = intervention_from_payload(payload)
    if block and block.get("action") in (
        "rewrite_outline",
        "reset_body",
        "edit_plot",
        "continue",
    ):
        return block["action"]
    return None


def detect_revision_intent(goal: str, conversation_history=None) -> None:
    """Removed — pass mission_intervention on the API instead."""
    return None


def detect_edit_plot_intent(goal: str, conversation_history=None) -> None:
    """Removed — use mission_intervention with action=edit_plot and edit_spec."""
    return None


def goal_changed_since_last_turn(goal: str, conversation_history=None) -> bool:
    """Removed — steer with message only triggers planning on the next turn."""
    return False
