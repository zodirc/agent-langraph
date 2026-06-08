"""Unified user event classification (optimization.md §4.3).

Single entry for classifying inbound user control events before planning /
interrupt / acknowledge layers. Consolidates steer tier hints, turn policy
signals, and explicit API control fields.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal, Optional

from app.services.foreground_execution import (
    INTERRUPT_P0,
    classify_steer_interrupt,
)
from app.services.interaction_goal import goal_is_mission_status_query

EventType = Literal[
    "new_task",
    "clarification",
    "interrupt",
    "redirect",
    "confirm",
    "reject",
    "resume",
    "status_query",
]

VALID_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "new_task",
        "clarification",
        "interrupt",
        "redirect",
        "confirm",
        "reject",
        "resume",
        "status_query",
    }
)


@dataclass(frozen=True)
class EventClassification:
    event_type: EventType
    event_id: str
    source: str
    reason: str
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "event_id": self.event_id,
            "source": self.source,
            "reason": self.reason,
            "confidence": round(float(self.confidence), 4),
        }


def _new_event_id() -> str:
    return str(uuid.uuid4())


def _goal_text(payload: dict[str, Any]) -> str:
    return str(payload.get("goal") or payload.get("message") or "").strip()


def _explicit_event_type(payload: dict[str, Any]) -> Optional[EventType]:
    raw = payload.get("event_type") or payload.get("control_event")
    if not raw:
        return None
    value = str(raw).strip().lower()
    if value in VALID_EVENT_TYPES:
        return value  # type: ignore[return-value]
    return None


def _is_follow_up_turn(state: dict[str, Any]) -> bool:
    """True only when continuing a prior session turn (session_turn > 1).

    Do not infer follow-up from conversation_history: prepare_session_turn always
    prepends the current user message before event_classification runs.
    """
    return int(state.get("session_turn") or 0) > 1


def _interrupt_signals(
    state: dict[str, Any],
    payload: dict[str, Any],
    goal: str,
) -> bool:
    if payload.get("foreground_preempt_pending"):
        return True
    ctx = state.get("interrupt_context") or {}
    if isinstance(ctx, dict):
        if ctx.get("cancel_requested") or ctx.get("pause_requested"):
            return True
        if str(ctx.get("control_state") or "") in {
            "INTERRUPT_REQUESTED",
            "CANCELLING",
            "CANCEL_REQUESTED",
        }:
            return True
    intervention = payload.get("intervention") if isinstance(payload.get("intervention"), dict) else {}
    tier = classify_steer_interrupt(
        goal,
        intervention=intervention or None,
        priority=int(payload.get("priority") or 0),
        preempt=bool(payload.get("preempt")),
    )
    if tier == INTERRUPT_P0:
        return True
    if bool(payload.get("preempt")) and intervention.get("force"):
        return True
    action = str(intervention.get("action") or "").lower()
    if action in {"stop", "cancel", "abort", "pause"}:
        return True
    return False


def _resume_signals(state: dict[str, Any], payload: dict[str, Any], goal: str) -> bool:
    if payload.get("resume") is True:
        return True
    if payload.get("resume_checkpoint_ref") or payload.get("resume_from_step_id"):
        return True
    ctx = state.get("interrupt_context") or {}
    if isinstance(ctx, dict) and ctx.get("resume_from_checkpoint"):
        return True
    from app.services.manuscript_service import is_continue_writing_goal

    if is_continue_writing_goal(goal):
        return True
    decision = payload.get("turn_policy_decision")
    if isinstance(decision, dict) and decision.get("intent") == "resume_mission":
        return True
    return False


def _redirect_signals(payload: dict[str, Any], goal: str) -> bool:
    if payload.get("replace_goal"):
        return True
    decision = payload.get("turn_policy_decision")
    if isinstance(decision, dict) and decision.get("intent") == "supersede_active_mission":
        return True
    intervention = payload.get("intervention") if isinstance(payload.get("intervention"), dict) else {}
    action = str(intervention.get("action") or "").lower()
    if action in {"rewrite", "rewrite_outline", "reset_body", "edit_plot", "redirect"}:
        return True
    if payload.get("steer_replan_mode") or payload.get("require_planning_after_steer"):
        if goal and not payload.get("preempt"):
            return True
    return False


def _confirm_signals(payload: dict[str, Any]) -> bool:
    if payload.get("confirm") is True:
        return True
    if payload.get("steer_intent_confirmed") or payload.get("steer_outcome_confirmed"):
        return True
    return False


def _reject_signals(payload: dict[str, Any], state: dict[str, Any]) -> bool:
    intervention = payload.get("intervention") if isinstance(payload.get("intervention"), dict) else {}
    if str(intervention.get("action") or "").lower() in {"reject", "decline"}:
        return True
    feedback = state.get("review_feedback") or payload.get("review_feedback")
    if isinstance(feedback, dict) and str(feedback.get("decision") or "").lower() == "reject":
        return True
    return False


def classify_user_event(
    state: dict[str, Any],
    *,
    payload: Optional[dict[str, Any]] = None,
) -> EventClassification:
    """
    Classify the current inbound user event.

    Priority: explicit override → interrupt → resume → status_query → confirm
    → reject → redirect → clarification → new_task.
    """
    payload = dict(payload or state.get("input_payload") or {})
    goal = _goal_text(payload)
    event_id = _new_event_id()

    explicit = _explicit_event_type(payload)
    if explicit is not None:
        return EventClassification(
            event_type=explicit,
            event_id=event_id,
            source="explicit",
            reason=f"payload event_type={explicit}",
        )

    if _interrupt_signals(state, payload, goal):
        return EventClassification(
            event_type="interrupt",
            event_id=event_id,
            source="interrupt_signal",
            reason="preempt/cancel/stop or P0 steer",
        )

    if _resume_signals(state, payload, goal):
        return EventClassification(
            event_type="resume",
            event_id=event_id,
            source="resume_signal",
            reason="continue mission or checkpoint resume",
        )

    if goal and goal_is_mission_status_query(goal):
        return EventClassification(
            event_type="status_query",
            event_id=event_id,
            source="status_query_pattern",
            reason="mission status / progress inquiry",
        )

    if _confirm_signals(payload):
        return EventClassification(
            event_type="confirm",
            event_id=event_id,
            source="confirm_signal",
            reason="user or steer confirmation gate",
        )

    if _reject_signals(payload, state):
        return EventClassification(
            event_type="reject",
            event_id=event_id,
            source="reject_signal",
            reason="intervention or review rejection",
        )

    if _redirect_signals(payload, goal):
        return EventClassification(
            event_type="redirect",
            event_id=event_id,
            source="redirect_signal",
            reason="goal replacement or supersede replan",
        )

    if _is_follow_up_turn(state) and goal:
        return EventClassification(
            event_type="clarification",
            event_id=event_id,
            source="follow_up_turn",
            reason="multi-turn constraint or supplement",
            confidence=0.85,
        )

    return EventClassification(
        event_type="new_task",
        event_id=event_id,
        source="default",
        reason="first-turn or fresh task",
    )
