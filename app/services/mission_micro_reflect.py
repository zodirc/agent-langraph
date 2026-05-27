"""Lightweight in-mission reflection when confidence is low (§5.11)."""

from __future__ import annotations

import json
from typing import Any

from app.config.prompts import REFLECTION_SYSTEM
from app.config.settings import settings
from app.runtime.state import AgentState


def maybe_micro_reflect(state: AgentState) -> dict[str, Any] | None:
    """
    Run at most one micro-reflection per mission step when confidence < threshold.
    Returns reflection dict or None.
    """
    if not settings.MISSION_MICRO_REFLECT_ENABLED:
        return None
    reasoning = state.get("reasoning_result") or {}
    confidence = float(reasoning.get("confidence", 1.0))
    if confidence >= settings.MISSION_MICRO_REFLECT_THRESHOLD:
        return None
    if int(state.get("mission_micro_reflect_count") or 0) >= 1:
        return None

    payload = {
        "reasoning_result": reasoning,
        "mission_step": state.get("mission_step"),
        "observation": state.get("observation"),
    }
    try:
        from app.services.llm_client import invoke_structured

        result = invoke_structured(
            "reflection",
            REFLECTION_SYSTEM,
            json.dumps(payload, ensure_ascii=False),
        )
        return {
            "critique": str(result.get("critique", "")),
            "retry_reasoning": bool(result.get("retry_reasoning", False)),
            "issues": list(result.get("issues") or []),
            "source": "mission_micro",
        }
    except Exception:
        issues = []
        if confidence < 0.5:
            issues.append(f"low confidence ({confidence})")
        return {
            "critique": "; ".join(issues) or "low confidence step",
            "retry_reasoning": bool(issues),
            "issues": issues,
            "source": "mission_micro_rules",
        }
