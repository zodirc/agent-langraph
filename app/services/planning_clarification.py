"""Low-confidence steer clarification (optimization.md §3.8)."""

from __future__ import annotations

from typing import Any

from app.config.settings import settings
from app.runtime.state import AgentState, merge_state


def _audit_confidence(audit: dict[str, Any]) -> float:
    for key in ("kind_confidence", "confidence"):
        try:
            return float(audit.get(key) or 1.0)
        except (TypeError, ValueError):
            continue
    return 1.0


def _interpretation_label(audit: dict[str, Any]) -> str:
    return str(
        audit.get("planned_route")
        or audit.get("inferred_kind")
        or audit.get("interpretation")
        or ""
    )


def build_steer_clarification_question(steer_text: str) -> str:
    steer = (steer_text or "").strip()
    if any(token in steer for token in ("原电影", "原作", "英文名", "Neo", "黑客帝国")):
        return (
            "需要确认一下：是保留中文叙述、但人物改用原片英文名（Neo / Morpheus / Trinity），"
            "还是连叙事语言也改成英文？"
        )
    return (
        "我对你的修改范围还不太确定：是要局部改几段设定，还是整体替换人物/章节？"
        "请用一句话说明要改哪一部分。"
    )


def maybe_apply_steer_clarification(state: AgentState) -> AgentState:
    """When route audit confidence is low and interpretations oscillate, ask one clarification."""
    payload = dict(state.get("input_payload") or {})
    if payload.get("steer_clarification_pending") or payload.get("steer_intent_pending_confirm"):
        return state

    audit = payload.get("route_audit") or {}
    if not isinstance(audit, dict):
        return state

    threshold = float(getattr(settings, "STEER_CLARIFICATION_CONFIDENCE", 0.3))
    confidence = _audit_confidence(audit)
    label = _interpretation_label(audit)
    history: list[str] = list(payload.get("steer_interpretation_history") or [])
    if label:
        history.append(label)
        history = history[-6:]
        payload["steer_interpretation_history"] = history

    if confidence > threshold:
        return merge_state(state, input_payload=payload)

    if len(history) < 2 or history[-1] == history[-2]:
        return merge_state(state, input_payload=payload)

    steer = str(payload.get("latest_steer_message") or payload.get("goal") or "")
    question = build_steer_clarification_question(steer)
    payload["steer_clarification_pending"] = True
    payload["steer_clarification_question"] = question
    payload["steer_intent_pending_confirm"] = True
    payload["steer_intent_confirmation"] = {
        "summary": question,
        "kind": "clarification",
        "confidence": confidence,
    }
    return merge_state(
        state,
        input_payload=payload,
        review_required=True,
        reasoning_result={
            "summary": question,
            "confidence": max(confidence, 0.5),
            "risk_level": "LOW",
            "structured": {"source": "steer_clarification", "kind": "clarification"},
        },
    )
