"""Unified user event classification (optimization.md §4.3).

Single entry for classifying inbound user control events before planning /
interrupt / acknowledge layers. Routing reads fsm_state and semantic signals only —
never client preempt/replace_goal or legacy replan flags.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal, Optional

from app.services.control_payload import strip_client_routing_hints
from app.services.foreground_execution import (
    INTERRUPT_P0,
    classify_steer_interrupt,
)
from app.services.interaction_goal import (
    goal_is_mission_status_query,
    goal_is_pure_greeting,
    goal_is_session_source_inquiry,
)

EventType = Literal[
    "new_task",
    "revision",
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
        "revision",
        "clarification",
        "interrupt",
        "redirect",
        "confirm",
        "reject",
        "resume",
        "status_query",
    }
)

_SUPPLEMENT_CUE_RE = re.compile(
    r"(补充|另外|还要|再加上|此外|顺便|约束|背景[：:]|设定[：:]|要求[：:])",
    re.IGNORECASE,
)
_DELIVERY_GOAL_RE = re.compile(
    r"(写一份|写一篇|生成一份|实现一个|制作一份|设计一份|编写一份|创作一份|"
    r"完成一份|帮我写|帮我做|写一个|做一个)",
    re.IGNORECASE,
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

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Optional[EventClassification]:
        if not isinstance(raw, dict):
            return None
        event_type = str(raw.get("event_type") or "").strip().lower()
        event_id = str(raw.get("event_id") or "").strip()
        if event_type not in VALID_EVENT_TYPES or not event_id:
            return None
        return cls(
            event_type=event_type,  # type: ignore[arg-type]
            event_id=event_id,
            source=str(raw.get("source") or "stamped"),
            reason=str(raw.get("reason") or ""),
            confidence=float(raw.get("confidence") or 1.0),
        )


def _new_event_id() -> str:
    return str(uuid.uuid4())


def _goal_text(payload: dict[str, Any]) -> str:
    return str(payload.get("goal") or payload.get("message") or "").strip()


def _meta_block(payload: dict[str, Any]) -> dict[str, Any]:
    meta = payload.get("meta")
    return meta if isinstance(meta, dict) else {}


def _is_user_resend(payload: dict[str, Any]) -> bool:
    if payload.get("resend") is True:
        return True
    return bool(_meta_block(payload).get("resend"))


def stamp_inbound_classification(
    payload: dict[str, Any],
    classification: EventClassification,
) -> dict[str, Any]:
    """Attach authoritative L1 classification to inbound payload."""
    out = dict(payload)
    out["event_classification"] = classification.to_dict()
    out["inbound_event_id"] = classification.event_id
    return out


def classification_from_payload(payload: dict[str, Any]) -> Optional[EventClassification]:
    """Return stamped classification when inbound_event_id matches."""
    stamped = payload.get("event_classification")
    if not isinstance(stamped, dict):
        return None
    inbound_id = str(payload.get("inbound_event_id") or stamped.get("event_id") or "")
    if not inbound_id or str(stamped.get("event_id") or "") != inbound_id:
        return None
    return EventClassification.from_dict(stamped)


def resolve_inbound_event(
    state: dict[str, Any],
    *,
    payload: Optional[dict[str, Any]] = None,
) -> EventClassification:
    """Single L1 classification entry — reuse stamp when already resolved."""
    payload = strip_client_routing_hints(dict(payload or state.get("input_payload") or {}))
    existing = classification_from_payload(payload)
    if existing is not None:
        return existing
    return classify_user_event(state, payload=payload)


def _explicit_event_type(payload: dict[str, Any]) -> Optional[EventType]:
    raw = payload.get("event_type") or payload.get("control_event")
    if not raw:
        return None
    value = str(raw).strip().lower()
    if value in VALID_EVENT_TYPES:
        return value  # type: ignore[return-value]
    return None


def _mission_steerable(state: dict[str, Any], payload: dict[str, Any]) -> bool:
    """True when an active run can be steered / superseded (not idle QA)."""
    _ = payload
    from app.services.graph_run_registry import executor_active_for_state

    return executor_active_for_state(state)


def _is_follow_up_turn(state: dict[str, Any]) -> bool:
    """True only when continuing a prior session turn (session_turn > 1)."""
    return int(state.get("session_turn") or 0) > 1


def _conversation_history(state: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    history = state.get("conversation_history")
    if isinstance(history, list) and history:
        return history
    payload_history = payload.get("conversation_history")
    if isinstance(payload_history, list):
        return payload_history
    return []


def _prior_substantive_task_goal(prior_goal: str) -> bool:
    """True when the prior user turn started a real task (not greeting / chitchat)."""
    text = (prior_goal or "").strip()
    if not text or goal_is_pure_greeting(text):
        return False
    if _DELIVERY_GOAL_RE.search(text):
        return True
    from app.services.session.turn_policy import _goal_requires_steer_replan

    if _goal_requires_steer_replan(text):
        return True
    return len(text) > 20


def _prior_user_goals(state: dict[str, Any], payload: dict[str, Any], goal: str) -> list[str]:
    """User messages before the current inbound goal."""
    user_msgs = [
        str(m.get("content") or "").strip()
        for m in _conversation_history(state, payload)
        if str(m.get("role") or "") == "user" and str(m.get("content") or "").strip()
    ]
    if user_msgs and goal and user_msgs[-1] == goal:
        user_msgs = user_msgs[:-1]
    return user_msgs


def _is_constraint_supplement(
    state: dict[str, Any],
    payload: dict[str, Any],
    goal: str,
) -> bool:
    """True when the user is refining an existing substantive task, not starting fresh."""
    if not goal or not _is_follow_up_turn(state):
        return False

    prior_users = _prior_user_goals(state, payload, goal)
    if not prior_users:
        return False

    prior_goal = prior_users[-1]
    if not _prior_substantive_task_goal(prior_goal):
        return False

    if _SUPPLEMENT_CUE_RE.search(goal):
        return True

    from app.services.session.turn_policy import _goal_requires_steer_replan

    if _goal_requires_steer_replan(goal):
        return False

    if _DELIVERY_GOAL_RE.search(goal):
        return False

    if len(goal) <= 96:
        return True

    return False


def _explicit_interrupt(
    state: dict[str, Any],
    payload: dict[str, Any],
    goal: str,
) -> bool:
    """Explicit control signals — stop/cancel, force, foreground preempt, interrupt_context."""
    steerable = _mission_steerable(state, payload)
    ip = state.get("input_payload") or {}
    if ip.get("foreground_preempt_pending"):
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
    if intervention.get("force") and steerable:
        return True
    action = str(intervention.get("action") or "").lower()
    if action in {"stop", "cancel", "abort", "pause"}:
        return True
    return False


def _heuristic_interrupt(
    state: dict[str, Any],
    payload: dict[str, Any],
    goal: str,
) -> bool:
    """P0 content-regex steer — heuristic only; must not beat explicit resend."""
    if not _mission_steerable(state, payload):
        return False
    from app.runtime.state import TaskStatus

    if str(state.get("status") or "") == TaskStatus.COMPLETED.value:
        from app.services.session.turn_policy import _goal_requires_steer_replan

        if _goal_requires_steer_replan(goal):
            return False
    intervention = payload.get("intervention") if isinstance(payload.get("intervention"), dict) else {}
    tier = classify_steer_interrupt(
        goal,
        intervention=intervention or None,
        priority=int(payload.get("priority") or 0),
        preempt=False,
    )
    return tier == INTERRUPT_P0


def _resume_signals(state: dict[str, Any], payload: dict[str, Any], goal: str) -> bool:
    if _is_user_resend(payload):
        return False
    from app.runtime.state import TaskStatus

    status = str(state.get("status") or "")
    if status.endswith("FAILED") or status in (
        TaskStatus.FAILED.value,
        TaskStatus.TOOL_FAILED.value,
        TaskStatus.REASON_FAILED.value,
    ):
        from app.services.session.turn_policy import _goal_requires_steer_replan

        if _goal_requires_steer_replan(goal):
            return False
        if not (
            payload.get("resume") is True
            or payload.get("resume_checkpoint_ref")
            or payload.get("resume_from_step_id")
        ):
            return False
    if payload.get("resume") is True:
        return True
    if payload.get("resume_checkpoint_ref") or payload.get("resume_from_step_id"):
        return True
    ctx = state.get("interrupt_context") or {}
    if isinstance(ctx, dict) and ctx.get("resume_from_checkpoint"):
        return True
    return False


def _resend_steer_redirect(state: dict[str, Any], payload: dict[str, Any], goal: str) -> bool:
    """Resend with a steer correction while an executor is live → redirect, not new_task."""
    if not _is_user_resend(payload):
        return False
    if not _mission_steerable(state, payload):
        return False
    from app.services.session.turn_policy import _goal_requires_steer_replan

    return bool(goal) and _goal_requires_steer_replan(goal)


def _redirect_signals(state: dict[str, Any], payload: dict[str, Any], goal: str) -> bool:
    from app.services.session_fsm import routing_needs_replan

    decision = payload.get("turn_policy_decision")
    if isinstance(decision, dict) and decision.get("intent") == "supersede_active_mission":
        return True
    if routing_needs_replan(state) and goal:
        return True
    if not _mission_steerable(state, payload):
        return False
    intervention = payload.get("intervention") if isinstance(payload.get("intervention"), dict) else {}
    action = str(intervention.get("action") or "").lower()
    if action in {"rewrite", "rewrite_outline", "reset_body", "edit_plot", "redirect"}:
        return True
    return False


def _confirm_signals(payload: dict[str, Any], state: dict[str, Any] | None = None) -> bool:
    if payload.get("confirm") is True:
        return True
    if payload.get("steer_intent_confirmed") or payload.get("steer_outcome_confirmed"):
        return True
    goal = _goal_text(payload)
    if goal and state:
        from app.services.writing_pending import goal_looks_like_confirm_only, pending_from_payload

        prior = state.get("input_payload") or {}
        if goal_looks_like_confirm_only(goal) and pending_from_payload(prior):
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

    Priority: explicit override → explicit-interrupt → resend → heuristic-interrupt
    → resume → status_query → confirm → reject → redirect → clarification → new_task.
    """
    payload = strip_client_routing_hints(dict(payload or state.get("input_payload") or {}))
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

    if _explicit_interrupt(state, payload, goal):
        return EventClassification(
            event_type="interrupt",
            event_id=event_id,
            source="explicit_interrupt",
            reason="stop/cancel/force or foreground preempt",
        )

    if _is_user_resend(payload):
        if _resend_steer_redirect(state, payload, goal):
            return EventClassification(
                event_type="redirect",
                event_id=event_id,
                source="user_resend_steer",
                reason="resend steer correction on active mission — replan stream",
            )
        return EventClassification(
            event_type="new_task",
            event_id=event_id,
            source="user_resend",
            reason="user resend retry — not resume",
        )

    if _heuristic_interrupt(state, payload, goal):
        return EventClassification(
            event_type="interrupt",
            event_id=event_id,
            source="heuristic_interrupt",
            reason="P0 steer content regex",
        )

    if _resume_signals(state, payload, goal):
        return EventClassification(
            event_type="resume",
            event_id=event_id,
            source="resume_signal",
            reason="continue mission or checkpoint resume",
        )

    if goal and goal_is_mission_status_query(goal):
        if _mission_steerable(state, payload):
            return EventClassification(
                event_type="status_query",
                event_id=event_id,
                source="status_query_pattern",
                reason="mission status / progress inquiry",
            )

    if _confirm_signals(payload, state):
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

    if _redirect_signals(state, payload, goal):
        return EventClassification(
            event_type="redirect",
            event_id=event_id,
            source="redirect_signal",
            reason="goal replacement or supersede replan",
        )

    if goal and goal_is_session_source_inquiry(goal):
        return EventClassification(
            event_type="clarification",
            event_id=event_id,
            source="session_source_inquiry",
            reason="ask whether session source material is loaded",
            confidence=0.9,
        )

    if _is_constraint_supplement(state, payload, goal):
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
