"""Thin execution profiles — planning skip → reasoning tier (no canned answers).

When planning uses qa_thin_skip / engineering_thin_skip, payload carries
``thin_execution_profile``; reasoning reads it for model purpose + stream policy.
"""

from __future__ import annotations

from typing import Any

# Profiles map to llm_client purpose keys (see config max_tokens_by_purpose).
_PROFILE_TO_LLM_PURPOSE: dict[str, str] = {
    "qa_direct": "routing",
    "engineering_direct": "routing",
}


def thin_execution_profile(payload: dict[str, Any] | None) -> str | None:
    raw = (payload or {}).get("thin_execution_profile")
    text = str(raw or "").strip()
    return text or None


def reasoning_llm_purpose(state: dict[str, Any]) -> str:
    """LLM purpose for reasoning invoke/stream (routing = low token budget)."""
    payload = state.get("input_payload") or {}
    profile = thin_execution_profile(payload)
    if profile:
        return _PROFILE_TO_LLM_PURPOSE.get(profile, "routing")
    return "reasoning"


def thin_execution_active(state: dict[str, Any]) -> bool:
    return thin_execution_profile(state.get("input_payload") or {}) is not None


def should_stream_thinking_for_state(state: dict[str, Any]) -> bool:
    """Thin profiles answer directly — no thinking stream."""
    if thin_execution_active(state):
        return False
    from app.services.reasoning_trace import thinking_stream_enabled

    return thinking_stream_enabled()
