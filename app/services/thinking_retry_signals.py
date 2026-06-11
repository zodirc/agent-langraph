"""Thinking-stage retry signals (retry_reasoning before user-visible stream)."""

from __future__ import annotations

from typing import Any

from app.config.settings import settings
from app.runtime.state import AgentState, merge_state


def _event_log_issues(state: AgentState) -> list[str]:
    from app.services.turn_event_log import get_turn_event_log

    issues: list[str] = []
    for event in get_turn_event_log(state).failure_events():
        detail = event.detail or {}
        if event.event_type == "plan_rejected":
            for item in detail.get("issues") or []:
                issues.append(f"plan_rejected: {item}")
        elif event.event_type == "tool_blocked":
            for item in detail.get("issues") or []:
                issues.append(f"tool_blocked: {item}")
        else:
            issues.append(f"{event.event_type}: {event.subject}")
    return issues[:5]


def thinking_context_issues(state: AgentState) -> list[str]:
    """Issues detectable before generation from facts / event log / payload."""
    issues = list(_event_log_issues(state))
    payload = state.get("input_payload") or {}
    feedback = payload.get("thinking_retry_feedback")
    if isinstance(feedback, dict):
        for item in feedback.get("issues") or []:
            issues.append(str(item))
    return list(dict.fromkeys(issues))


def reasoning_result_needs_retry(reasoning_result: dict[str, Any]) -> bool:
    structured = reasoning_result.get("structured") or {}
    if structured.get("parser_fallback"):
        return False
    warnings = list(structured.get("fact_warnings") or [])
    confidence = float(reasoning_result.get("confidence", 1.0))
    if warnings:
        return True
    return confidence < 0.6


def max_thinking_retries() -> int:
    return max(1, int(getattr(settings, "REFLECTION_MAX_ROUNDS", 2)))


def apply_thinking_retry_feedback(state: AgentState) -> AgentState:
    """Stamp pre-generation feedback from context governance."""
    issues = thinking_context_issues(state)
    if not issues:
        return state
    payload = dict(state.get("input_payload") or {})
    payload["thinking_retry_feedback"] = {"issues": issues}
    return merge_state(state, input_payload=payload)


def build_reasoning_retry_context(base_context: dict[str, Any], state: AgentState) -> dict[str, Any]:
    """Enrich LLM context when a non-stream retry is required."""
    issues = thinking_context_issues(state)
    structured = (state.get("reasoning_result") or {}).get("structured") or {}
    issues.extend(str(w) for w in (structured.get("fact_warnings") or [])[:5])
    issues = list(dict.fromkeys(issues))
    if not issues:
        return base_context
    ctx = dict(base_context)
    ctx["reasoning_retry_feedback"] = {
        "issues": issues,
        "prior_summary": str((state.get("reasoning_result") or {}).get("summary") or "")[:800],
    }
    return ctx
