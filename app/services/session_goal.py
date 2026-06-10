"""Backward-compatible re-exports — see app.services.session.turn_policy."""

from app.services.session.turn_policy import (
    TurnDecision,
    apply_qa_turn_isolation,
    classify_turn_intent,
    is_ephemeral_qa_goal,
    resolve_session_turn,
)

__all__ = [
    "TurnDecision",
    "apply_qa_turn_isolation",
    "classify_turn_intent",
    "is_ephemeral_qa_goal",
    "resolve_session_turn",
]
