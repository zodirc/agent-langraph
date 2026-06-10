"""Session turn policy — generic per-turn classification (unified-core WP-6).

The mission resume/supersede trichotomy collapsed with the mission runtime:
every inbound message simply drives a new unified-loop turn. What survives is
the lightweight audit decision plus the steer-correction heuristic used by
event classification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from app.services.session.config import SessionTurnPolicyConfig, load_session_turn_policy_config

TurnIntent = Literal["new_turn", "isolate_qa"]

_CONTINUE_GOAL_RE = re.compile(
    r"(续写|继续写|继续|追加|下一章|接着写|写下去|\bappend\b|\bcontinue\b)",
    re.IGNORECASE,
)


@dataclass
class TurnDecision:
    intent: TurnIntent
    source: str
    kind: str | None = None
    confidence: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "source": self.source,
            "kind": self.kind,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
        }


def is_continue_writing_goal(goal: str) -> bool:
    text = (goal or "").strip()
    if not text:
        return False
    if _CONTINUE_GOAL_RE.search(text):
        return True
    return len(text) < 24 and any(k in text for k in ("续", "继续", "接着"))


def _matches_continue_patterns(goal: str, cfg: SessionTurnPolicyConfig) -> bool:
    text = (goal or "").strip()
    if not text:
        return False
    if is_continue_writing_goal(text):
        return True
    return any(p.search(text) for p in cfg.continue_goal_patterns)


def _goal_requires_steer_replan(goal: str) -> bool:
    """True for mid-task direction corrections that need replan, not casual QA."""
    text = (goal or "").strip()
    if not text or is_continue_writing_goal(text):
        return False
    if len(text) <= 24 and re.match(
        r"^(你好|您好|hello|hi|hey|嗨|在吗|在么|哈喽|嗨喽)\b",
        text,
        re.IGNORECASE,
    ):
        return False
    if re.fullmatch(r"(你好|您好|hello|hi|hey|嗨)[!.?，,\s]*", text, re.IGNORECASE):
        return False
    correction_cues = re.compile(
        r"(不要|别用|改用|换成|应该|需要|认为|改一下|修改|调整|纠正|更正|"
        r"instead|rather|should not|should use|change the|modify the|correct)",
        re.IGNORECASE,
    )
    return bool(correction_cues.search(text))


def resolve_session_turn(
    state: dict[str, Any],
    payload: dict[str, Any],
    goal: str,
    *,
    incoming: dict[str, Any] | None = None,
    turn_cfg: SessionTurnPolicyConfig | None = None,
    route_cfg: Any = None,
) -> TurnDecision:
    """Every message is a fresh unified-loop turn; decision kept for audit."""
    _ = (state, payload, incoming, route_cfg)
    turn_cfg = turn_cfg or load_session_turn_policy_config()
    if _matches_continue_patterns(goal, turn_cfg):
        return TurnDecision(
            intent="new_turn",
            source="continue_signal",
            reason="continue request — unified loop replans from current artifacts",
        )
    return TurnDecision(
        intent="new_turn",
        source="default",
        reason="unified loop: every message drives a new turn",
    )


def classify_turn_intent(
    goal: str,
    *,
    mission_active: bool = False,
    turn_cfg: SessionTurnPolicyConfig | None = None,
    route_cfg: Any = None,
) -> TurnIntent:
    _ = mission_active
    return resolve_session_turn({}, {"goal": goal}, goal, turn_cfg=turn_cfg, route_cfg=route_cfg).intent


def is_ephemeral_qa_goal(goal: str) -> bool:
    """Legacy helper — no mission to isolate from in the unified loop."""
    return False


def apply_qa_turn_isolation(payload: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    """Legacy no-op: QA isolation is meaningless without a mission runtime."""
    _ = existing
    return dict(payload)


def apply_revision_turn_isolation(
    payload: dict[str, Any], existing: dict[str, Any]
) -> dict[str, Any]:
    """Legacy no-op: revision isolation is meaningless without a mission runtime."""
    _ = existing
    return dict(payload)
