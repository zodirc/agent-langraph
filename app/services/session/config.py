"""Load session turn policy from settings (config.yaml)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.config.settings import settings


_DEFAULT_CONTINUE_PATTERNS = (
    r"(?i)(续写|继续写|继续|追加|下一章|接着写|写下去|append|continue)",
)


def _compile_patterns(raw: Any, defaults: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    items: list[str] = list(defaults)
    if isinstance(raw, list):
        items = [str(x).strip() for x in raw if str(x).strip()]
    out: list[re.Pattern[str]] = []
    for text in items:
        try:
            out.append(re.compile(text, re.IGNORECASE | re.MULTILINE))
        except re.error:
            continue
    return tuple(out)


def _frozenset(raw: Any, default: frozenset[str]) -> frozenset[str]:
    if isinstance(raw, list):
        return frozenset(str(x) for x in raw)
    return default


@dataclass(frozen=True)
class SessionTurnPolicyConfig:
    """
    Session turn policy — explicit state machine with config fast paths + LLM gray zone.

    When a writing mission is active, ``default_suspend_when_mission_active`` keeps the
    mission paused until an explicit resume signal (continue, intervention, high-confidence
    manuscript kind, or LLM ``resume_writing``).
    """

    enabled: bool = True
    default_suspend_when_mission_active: bool = True
    resume_on_kinds: frozenset[str] = frozenset({"manuscript"})
    isolate_on_kinds: frozenset[str] = frozenset({"qa"})
    min_kind_confidence: float = 0.35
    isolate_when_empty_goal: bool = True
    continue_goal_patterns: tuple[re.Pattern[str], ...] = ()
    llm_intent_enabled: bool = True
    llm_intent_min_confidence: float = 0.55
    llm_intent_max_goal_chars: int = 2000
    audit_decisions: bool = True


def load_session_turn_policy_config() -> SessionTurnPolicyConfig:
    raw = getattr(settings, "SESSION_TURN_POLICY_CONFIG", None)
    if not isinstance(raw, dict):
        session_raw = getattr(settings, "SESSION_CONFIG", None)
        tp = session_raw.get("turn_policy") if isinstance(session_raw, dict) else None
        raw = tp if isinstance(tp, dict) else {}

    llm_raw = raw.get("llm_intent")
    llm: dict[str, Any] = llm_raw if isinstance(llm_raw, dict) else {}

    return SessionTurnPolicyConfig(
        enabled=bool(raw.get("enabled", True)),
        default_suspend_when_mission_active=bool(
            raw.get("default_suspend_when_mission_active", True)
        ),
        resume_on_kinds=_frozenset(
            raw.get("resume_on_kinds"),
            SessionTurnPolicyConfig.resume_on_kinds,
        ),
        isolate_on_kinds=_frozenset(
            raw.get("isolate_on_kinds"),
            SessionTurnPolicyConfig.isolate_on_kinds,
        ),
        min_kind_confidence=float(raw.get("min_kind_confidence", 0.35)),
        isolate_when_empty_goal=bool(raw.get("isolate_when_empty_goal", True)),
        continue_goal_patterns=_compile_patterns(
            raw.get("continue_goal_patterns"),
            _DEFAULT_CONTINUE_PATTERNS,
        ),
        llm_intent_enabled=bool(llm.get("enabled", raw.get("llm_intent_enabled", True))),
        llm_intent_min_confidence=float(
            llm.get("min_confidence", raw.get("llm_intent_min_confidence", 0.55))
        ),
        llm_intent_max_goal_chars=int(
            llm.get("max_goal_chars", raw.get("llm_intent_max_goal_chars", 2000))
        ),
        audit_decisions=bool(raw.get("audit_decisions", True)),
    )
