"""
User feedback loop — memory tags + optional pack param tuning (Ch9).
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from app.domain.memory import MemoryRecord
from app.domain.packs.registry import resolve_mission_pack
from app.services.memory_store import get_memory_store
from app.services.pack_params_store import get_pack_params_store
from app.services.state_store import get_state_store


def _normalize_rating(rating: Any) -> int:
    if isinstance(rating, str):
        mapping = {"positive": 5, "negative": 1, "neutral": 3}
        if rating.lower() in mapping:
            return mapping[rating.lower()]
    try:
        value = int(rating)
    except (TypeError, ValueError):
        return 3
    return max(1, min(5, value))


def record_feedback(
    *,
    task_id: str,
    user_id: str,
    rating: Any,
    comment: str = "",
    outcome: str = "unknown",
    tags: Optional[list[str]] = None,
    experiment_tag: str = "",
) -> dict[str, Any]:
    """
    Persist feedback as retrievable memory and optionally tune domain pack params.
    """
    store = get_state_store()
    state = store.load(task_id)
    if not state:
        raise ValueError(f"Task not found: {task_id}")

    if state.get("user_id") != user_id:
        raise ValueError("Cannot submit feedback for another user's task")

    score = _normalize_rating(rating)
    outcome_norm = str(outcome or "unknown").lower()
    extra_tags = list(tags or [])
    feedback_tags = [
        "feedback",
        f"feedback_rating:{score}",
        f"feedback_outcome:{outcome_norm}",
        *extra_tags,
    ]
    if score >= 4:
        feedback_tags.append("feedback_positive")
    elif score <= 2:
        feedback_tags.append("feedback_negative")

    summary = (comment or "").strip() or f"User feedback rating={score} outcome={outcome_norm}"
    reasoning = state.get("reasoning_result") or {}
    payload = {
        "rating": score,
        "outcome": outcome_norm,
        "comment": comment,
        "policy_result": state.get("policy_result"),
        "task_type": state.get("task_type"),
        "reasoning_summary": (reasoning.get("summary") or "")[:500],
    }

    memory_store = get_memory_store()
    record = MemoryRecord(
        memory_id=str(uuid.uuid4()),
        task_id=task_id,
        session_id=str(state.get("session_id") or task_id),
        user_id=user_id,
        task_type=str(state.get("task_type", "qa")),
        summary=summary[:2000],
        tags=feedback_tags,
        payload=payload,
    )
    memory_store.write(record, memory_type="feedback")

    exp_tag = (experiment_tag or "").strip()
    if exp_tag:
        feedback_tags.append(f"experiment:{exp_tag}")
        record.tags = feedback_tags

    pack_name = "single_turn"
    mission = state.get("mission") or {}
    if mission.get("kind"):
        pack_name = str(mission["kind"])
    else:
        try:
            pack = resolve_mission_pack(
                task_type=state.get("task_type"),
                payload=state.get("input_payload") or {},
            )
            pack_name = pack.name
        except KeyError:
            pass

    pack_params = get_pack_params_store().bump_from_feedback(
        pack_name,
        rating=score,
        outcome=outcome_norm,
        experiment_tag=exp_tag or "",
    )

    return {
        "task_id": task_id,
        "memory_id": record.memory_id,
        "rating": score,
        "outcome": outcome_norm,
        "tags": feedback_tags,
        "pack_name": pack_name,
        "pack_params_version": pack_params.get("_version"),
        "pack_params": {k: v for k, v in pack_params.items() if not str(k).startswith("_")},
    }
