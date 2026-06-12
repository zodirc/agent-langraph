"""Pending writing delivery — confirm inherits prior kickoff/replot intent."""

from __future__ import annotations

import re
from typing import Any, Mapping

_CONFIRM_GOAL_RE = re.compile(
    r"^(?:确认|好的|可以|开始吧|没问题|同意|ok|okay|yes)[\s!！。,，.?？~…]*$",
    re.IGNORECASE,
)


def goal_looks_like_confirm_only(goal: str) -> bool:
    text = (goal or "").strip()
    return bool(text) and bool(_CONFIRM_GOAL_RE.match(text))


def pending_from_payload(payload: Mapping[str, Any] | dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    raw = payload.get("pending_writing_delivery")
    return dict(raw) if isinstance(raw, dict) and raw.get("operator") else None


def infer_pending_operator(goal: str, state: Mapping[str, Any] | dict[str, Any] | None) -> str | None:
    from app.services.writing_intent_classifier import classify_writing_operator

    op = classify_writing_operator(goal, state)
    return str(op) if op else None


def record_pending_writing_delivery(state: dict[str, Any]) -> dict[str, Any]:
    """Stamp pending delivery when a writing turn ends on an explicit user-input ask."""
    from app.runtime.state import merge_state
    from app.services.writing_context import (
        turn_has_persisted_write,
        writing_explicit_ask,
        writing_intent_active,
    )

    if not writing_intent_active(state):
        return state
    if turn_has_persisted_write(state.get("tool_results") or []):
        return state
    reasoning = state.get("reasoning_result") or {}
    summary = str(reasoning.get("summary") or state.get("final_answer") or "")
    if not writing_explicit_ask(summary):
        return state
    if writing_false_promise_without_write(summary):
        return state

    payload = dict(state.get("input_payload") or {})
    goal = str(payload.get("goal") or payload.get("query") or "").strip()
    operator = str(payload.get("writing_operator") or "") or infer_pending_operator(goal, state)
    if not operator:
        return state

    payload["pending_writing_delivery"] = {
        "operator": operator,
        "goal": goal,
        "interaction_goal": "delivery",
        "source": "writing_explicit_ask",
    }
    return merge_state(state, input_payload=payload)


def apply_confirm_pending_delivery(
    existing: Mapping[str, Any] | dict[str, Any],
    merged: dict[str, Any],
) -> dict[str, Any]:
    """On confirm, restore the deferred writing operator and delivery goal."""
    confirm = merged.get("confirm") is True or str(merged.get("confirm_action") or "") == "true"
    goal = str(merged.get("goal") or merged.get("query") or "").strip()
    if not confirm and not goal_looks_like_confirm_only(goal):
        return merged

    prior_payload = existing.get("input_payload") or {}
    pending = pending_from_payload(prior_payload) or pending_from_payload(merged)
    if not pending:
        return merged

    out = dict(merged)
    effective_goal = str(pending.get("goal") or "").strip()
    if effective_goal:
        out["goal"] = effective_goal
        out["query"] = effective_goal
    operator = str(pending.get("operator") or "").strip()
    if operator:
        out["writing_operator"] = operator
    out["interaction_goal"] = str(pending.get("interaction_goal") or "delivery")
    out["interaction_goal_confidence"] = max(
        float(out.get("interaction_goal_confidence") or 0.0), 0.85
    )
    out["force_write_after_reads"] = operator in ("kickoff_body", "append", "rewrite", "polish")
    out.pop("pending_writing_delivery", None)
    out["pending_writing_delivery_consumed"] = pending
    intent = dict(out.get("writing_intent") or prior_payload.get("writing_intent") or {})
    intent["enabled"] = True
    intent["source"] = intent.get("source") or "pending_confirm"
    out["writing_intent"] = intent
    if confirm:
        out["confirm"] = True
    return out


def writing_false_promise_without_write(answer_text: str) -> bool:
    """
    True when the model promises imminent writing while asking for confirm —
    not a genuine missing-info terminal for delivery turns.
    """
    text = (answer_text or "").strip()
    if not text:
        return False
    if re.search(r"(请补充|请提供|需要你提供|需您提供|缺少|没有.{0,6}资料|用户提供)", text):
        return False
    promise = re.search(
        r"(马上|立即|这就|现在开始|开始写|开始撰写|撰写第|写第.{0,3}章|落盘|写入正文)",
        text,
    )
    confirm_ask = re.search(r"(确认|是否同意|可以吗|可否开始|需要您确认)", text)
    return bool(promise and confirm_ask)
