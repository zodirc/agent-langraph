"""Session goal drift detection (§2.3 / §4.1)."""

from __future__ import annotations

import re
from typing import Any

from app.runtime.state import AgentState
from app.services.conversation_context import (
    conversation_history_for_llm,
    conversation_history_from_state,
)

_TOPIC_SHIFT_RE = re.compile(
    r"(?i)\b(instead|actually|forget|switch|change topic|by the way|另外|换个|不要.*了|改成)\b"
)


def goals_aligned(confirmed_summary: str, planning_goal: str) -> bool:
    if not planning_goal or not confirmed_summary:
        return True
    a = {t.lower() for t in re.findall(r"[\w\u4e00-\u9fff]+", confirmed_summary) if len(t) > 1}
    b = {t.lower() for t in re.findall(r"[\w\u4e00-\u9fff]+", planning_goal) if len(t) > 1}
    if not a or not b:
        return True
    overlap = len(a & b) / min(len(a), len(b))
    return overlap >= 0.2


def detect_task_drift(state: AgentState | dict[str, Any]) -> dict[str, Any]:
    """
    Detect whether current turn diverges from prior retrieval focus or active mission goal.

    Returns {drifted, prior_goal, current_goal, reason, drift_type?, ...}.
    """
    payload = state.get("input_payload") or {}
    current = str(
        payload.get("goal") or payload.get("query") or payload.get("question") or ""
    ).strip()

    history = conversation_history_for_llm(conversation_history_from_state(state))
    prior = ""
    for msg in reversed(history):
        if str(msg.get("role")) == "user":
            text = str(msg.get("content") or "").strip()
            if text and text != current:
                prior = text
                break

    if not prior or not current:
        return {"drifted": False, "prior_goal": prior, "current_goal": current, "reason": ""}

    if _TOPIC_SHIFT_RE.search(current):
        return {
            "drifted": True,
            "prior_goal": prior[:200],
            "current_goal": current[:200],
            "reason": "explicit_topic_shift",
        }

    prior_tokens = {t.lower() for t in re.findall(r"[\w\u4e00-\u9fff]+", prior) if len(t) > 2}
    curr_tokens = {t.lower() for t in re.findall(r"[\w\u4e00-\u9fff]+", current) if len(t) > 2}
    if prior_tokens and curr_tokens:
        overlap = len(prior_tokens & curr_tokens) / min(len(prior_tokens), len(curr_tokens))
        if overlap < 0.15 and len(current.split()) >= 4:
            return {
                "drifted": True,
                "prior_goal": prior[:200],
                "current_goal": current[:200],
                "reason": "low_topic_overlap",
            }
    return {"drifted": False, "prior_goal": prior, "current_goal": current, "reason": ""}
