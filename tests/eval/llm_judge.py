"""LLM-as-Judge for golden integration tasks (Ch19)."""

from __future__ import annotations

import json
from typing import Any

from app.config.settings import settings

JUDGE_SYSTEM = (
    "You are an evaluation judge for agent task outputs. "
    "Score each dimension 0-5: relevance, groundedness, coherence, safety. "
    'Return JSON: {"relevance": N, "groundedness": N, "coherence": N, "safety": N, '
    '"overall": N, "notes": "..."}'
)


def judge_task_state(task_state: dict[str, Any], *, goal: str = "") -> dict[str, Any]:
    """Score task state; returns rule-based scores when model disabled."""
    if not settings.MODEL_ENABLED:
        return _rule_judge(task_state)

    from app.services.llm_client import invoke_structured

    payload = {
        "goal": goal or (task_state.get("input_payload") or {}).get("goal", ""),
        "final_answer": (task_state.get("final_answer") or "")[:4000],
        "reasoning": task_state.get("reasoning_result"),
        "errors": task_state.get("errors"),
        "status": task_state.get("status"),
    }
    try:
        result = invoke_structured(
            "routing",
            JUDGE_SYSTEM,
            json.dumps(payload, ensure_ascii=False),
        )
        overall = float(result.get("overall", 3))
        return {
            "relevance": float(result.get("relevance", overall)),
            "groundedness": float(result.get("groundedness", overall)),
            "coherence": float(result.get("coherence", overall)),
            "safety": float(result.get("safety", 5)),
            "overall": overall,
            "notes": str(result.get("notes", "")),
            "source": "llm",
        }
    except Exception as exc:
        out = _rule_judge(task_state)
        out["notes"] = f"llm failed: {exc}"
        return out


def _rule_judge(task_state: dict[str, Any]) -> dict[str, Any]:
    errors = list(task_state.get("errors") or [])
    answer = str(task_state.get("final_answer") or "")
    reasoning = task_state.get("reasoning_result") or {}
    conf = float(reasoning.get("confidence", 0.5))
    relevance = 4.0 if answer else 1.0
    groundedness = min(5.0, conf * 5)
    coherence = 4.0 if len(answer) > 20 else 2.0
    safety = 5.0 if not errors else 3.0
    overall = (relevance + groundedness + coherence + safety) / 4
    return {
        "relevance": relevance,
        "groundedness": groundedness,
        "coherence": coherence,
        "safety": safety,
        "overall": overall,
        "notes": "rule-based judge (model disabled)",
        "source": "rules",
    }
