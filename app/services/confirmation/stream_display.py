"""SSE

API display helpers — avoid duplicating gate content in final_answer."""

from __future__ import annotations

from typing import Any, Optional

from app.runtime.state import AgentState, TaskStatus

_GATE_REASONING_SOURCES = frozenset(
    {
        "steer_intent_confirmation",
        "steer_outcome_confirmation",
    }
)


def is_gate_reasoning_result(reasoning: Optional[dict[str, Any]]) -> bool:
    if not isinstance(reasoning, dict):
        return False
    structured = reasoning.get("structured")
    if not isinstance(structured, dict):
        return False
    return str(structured.get("source") or "") in _GATE_REASONING_SOURCES


def steer_confirmation_pending_any(payload: dict[str, Any]) -> bool:
    """Steer confirmation gates were removed with the mission runtime."""
    return False


def streamed_answer_revoked(state: AgentState) -> bool:
    """True when verification rejected after reasoning already produced a summary."""
    if str(state.get("status") or "") != TaskStatus.REJECTED.value:
        return False
    reasoning = state.get("reasoning_result") or {}
    return bool(str(reasoning.get("summary") or "").strip())


def rejection_detail(state: AgentState) -> str:
    errors = list(state.get("errors") or [])
    if errors:
        return str(errors[-1])
    guard = state.get("output_guard_result") or {}
    if isinstance(guard, dict):
        issues = guard.get("issues") or []
        if issues:
            return ", ".join(str(i) for i in issues[:3])
    decision = state.get("submission_decision") or {}
    if isinstance(decision, dict) and decision.get("reason"):
        return str(decision["reason"])
    return str(state.get("policy_result") or "verification_failed")


def client_final_answer(state: AgentState) -> Optional[str]:
    """User-visible final_answer; None when a steer gate owns the turn surface."""
    if str(state.get("status") or "") == TaskStatus.REJECTED.value:
        return None
    payload = state.get("input_payload") or {}
    if steer_confirmation_pending_any(payload):
        return None
    if is_gate_reasoning_result(state.get("reasoning_result")):
        return None
    answer = str(state.get("final_answer") or "").strip()
    if not answer:
        reasoning = state.get("reasoning_result") or {}
        if isinstance(reasoning, dict):
            answer = str(reasoning.get("summary") or "").strip()
    return answer or None


def build_gate_sse_fields(task_id: str, state: AgentState) -> dict[str, Any]:
    """Confirmation fields for SSE done / resume responses (gates removed)."""
    return {
        "steer_intent_pending_confirm": False,
        "steer_intent_confirmation": None,
        "steer_outcome_pending_confirm": False,
        "steer_outcome_confirmation": None,
    }
