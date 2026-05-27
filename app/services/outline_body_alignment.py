from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, Optional

from app.services.llm_client import invoke_structured


BodyAction = Literal["keep_append", "rewrite_body"]
ChangeLevel = Literal["minor", "moderate", "major"]


@dataclass
class AlignmentDecision:
    change_level: ChangeLevel
    body_action: BodyAction
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_level": self.change_level,
            "body_action": self.body_action,
            "reason": self.reason,
        }


_SYSTEM = """You are a writing-orchestration router.

Goal: decide whether a rewritten outline requires rewriting the existing manuscript body, or can safely continue appending.

Rules:
- Output ONE JSON object with keys: change_level (minor|moderate|major), body_action (keep_append|rewrite_body), reason (short).
- Prefer keep_append unless the outline meaningfully changes protagonist identity/goal, genre/world rules, timeline, POV, or invalidates already-written chapters.
- If the new outline is incompatible with the existing body tail/head, choose rewrite_body.
- Be conservative: only choose rewrite_body when continuing would be confusing or inconsistent.
"""


def decide_outline_body_alignment(
    *,
    outline_before_excerpt: str,
    outline_after_excerpt: str,
    body_tail_excerpt: str,
    body_total_chars: int,
    last_chapter_index: int,
    user_goal: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> AlignmentDecision:
    payload: dict[str, Any] = {
        "user_goal": (user_goal or "")[:400],
        "outline_before_excerpt": (outline_before_excerpt or "")[:6000],
        "outline_after_excerpt": (outline_after_excerpt or "")[:6000],
        "body_tail_excerpt": (body_tail_excerpt or "")[:3000],
        "body_total_chars": int(body_total_chars or 0),
        "last_chapter_index": int(last_chapter_index or 0),
        "extra": extra or {},
    }
    result = invoke_structured("routing", _SYSTEM, json.dumps(payload, ensure_ascii=False))
    level = str(result.get("change_level") or "minor")
    action = str(result.get("body_action") or "keep_append")
    reason = str(result.get("reason") or "").strip()

    if level not in ("minor", "moderate", "major"):
        level = "minor"
    if action not in ("keep_append", "rewrite_body"):
        action = "keep_append"
    if not reason:
        reason = "alignment decision"

    return AlignmentDecision(
        change_level=level,  # type: ignore[assignment]
        body_action=action,  # type: ignore[assignment]
        reason=reason,
    )

