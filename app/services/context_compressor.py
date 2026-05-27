"""Semantic context compression for long conversation histories."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from app.config.settings import settings
from app.services.memory_compress import MEMORY_COMPRESS_SYSTEM

logger = logging.getLogger(__name__)

CONTEXT_SUMMARY_SYSTEM = (
    MEMORY_COMPRESS_SYSTEM.replace(
        "Compress the episode into a dense summary",
        "Compress the conversation context into a structured summary",
    )
    + ' Return JSON: {"goal":"","hard_constraints":[],"executed_facts":[],"pending_todos":[],"open_risks":[]}'
)


@dataclass
class SemanticContextSummary:
    goal: str = ""
    hard_constraints: list[str] = field(default_factory=list)
    executed_facts: list[str] = field(default_factory=list)
    pending_todos: list[str] = field(default_factory=list)
    open_risks: list[str] = field(default_factory=list)
    raw_recent: list[dict[str, Any]] = field(default_factory=list)

    def to_system_message(self) -> dict[str, Any]:
        parts = [
            "[Semantic context summary]",
            f"Goal: {self.goal}" if self.goal else "",
        ]
        if self.hard_constraints:
            parts.append("Constraints: " + "; ".join(self.hard_constraints[:12]))
        if self.executed_facts:
            parts.append("Facts: " + "; ".join(self.executed_facts[:16]))
        if self.pending_todos:
            parts.append("Todos: " + "; ".join(self.pending_todos[:12]))
        if self.open_risks:
            parts.append("Risks: " + "; ".join(self.open_risks[:8]))
        content = "\n".join(p for p in parts if p).strip()
        if len(content) > settings.CONTEXT_COMPRESS_SUMMARY_MAX_CHARS:
            content = content[: settings.CONTEXT_COMPRESS_SUMMARY_MAX_CHARS] + "…"
        return {"role": "system", "content": content, "at": "semantic_compress"}


def _history_char_count(history: list[dict[str, Any]]) -> int:
    return sum(len(str(m.get("content", ""))) for m in history)


def compression_ratio(before_chars: int, after_chars: int) -> float:
    """Fraction of characters removed (0..1)."""
    if before_chars <= 0 or after_chars >= before_chars:
        return 0.0
    return 1.0 - (after_chars / before_chars)


def record_context_compress_metrics(
    history_before: list[dict[str, Any]],
    history_after: list[dict[str, Any]],
    *,
    method: str,
) -> float:
    before = _history_char_count(history_before)
    after = _history_char_count(history_after)
    ratio = compression_ratio(before, after)
    if ratio > 0:
        from app.services.metrics_service import get_metrics_service

        get_metrics_service().observe_context_compress_ratio(ratio, method=method)
    return ratio


def _extract_goal(state: dict[str, Any] | None, history: list[dict[str, Any]]) -> str:
    if not state:
        return ""
    mission = state.get("mission") or {}
    if isinstance(mission, dict) and mission.get("objective"):
        return str(mission["objective"]).strip()
    payload = state.get("input_payload") or {}
    if isinstance(payload, dict):
        for key in ("goal", "query", "question"):
            if payload.get(key):
                return str(payload[key]).strip()
    for msg in reversed(history):
        if msg.get("role") == "user" and msg.get("content"):
            return str(msg["content"]).strip()[:500]
    return ""


def _facts_from_turn_facts(state: dict[str, Any] | None) -> list[str]:
    if not state:
        return []
    turn_facts = state.get("turn_facts") or {}
    if not isinstance(turn_facts, dict):
        return []
    facts: list[str] = []
    for item in turn_facts.get("tools_executed") or []:
        if isinstance(item, dict):
            name = item.get("tool") or item.get("name")
            status = item.get("status")
            if name:
                facts.append(f"{name}: {status or 'ok'}")
        else:
            facts.append(str(item))
    for action in turn_facts.get("executed_actions") or []:
        facts.append(str(action))
    return facts[:20]


def _rule_based_summary(state: dict[str, Any] | None, history: list[dict[str, Any]]) -> SemanticContextSummary:
    keep = settings.CONTEXT_COMPRESS_KEEP_RECENT_TURNS
    recent = history[-keep:] if keep > 0 else []
    plan = (state or {}).get("plan") or []
    todos = [str(p) for p in plan[:8]] if isinstance(plan, list) else []
    constraints: list[str] = []
    payload = ((state or {}).get("input_payload") or {}) if state else {}
    if isinstance(payload, dict) and payload.get("risk_level"):
        constraints.append(f"risk_level={payload['risk_level']}")
    return SemanticContextSummary(
        goal=_extract_goal(state, history),
        hard_constraints=constraints,
        executed_facts=_facts_from_turn_facts(state),
        pending_todos=todos,
        open_risks=[],
        raw_recent=recent,
    )


def build_semantic_context_summary(
    history: list[dict[str, Any]],
    *,
    state: dict[str, Any] | None = None,
) -> SemanticContextSummary | None:
    if not settings.CONTEXT_COMPRESS_SEMANTIC_ENABLED or not history:
        return None
    if _history_char_count(history) < settings.CONTEXT_COMPRESS_MIN_CHARS_FOR_SEMANTIC:
        return None

    if settings.MODEL_ENABLED:
        try:
            from app.services.llm_client import invoke_structured

            user = json.dumps(
                {
                    "conversation": history[-40:],
                    "mission": (state or {}).get("mission"),
                    "turn_facts": (state or {}).get("turn_facts"),
                    "plan": (state or {}).get("plan"),
                },
                ensure_ascii=False,
            )[:16000]
            result = invoke_structured("summarization", CONTEXT_SUMMARY_SYSTEM, user)
            keep = settings.CONTEXT_COMPRESS_KEEP_RECENT_TURNS
            return SemanticContextSummary(
                goal=str(result.get("goal") or _extract_goal(state, history)).strip(),
                hard_constraints=[str(x) for x in result.get("hard_constraints") or []][:12],
                executed_facts=(
                    [str(x) for x in result.get("executed_facts") or []][:16]
                    or _facts_from_turn_facts(state)
                ),
                pending_todos=[str(x) for x in result.get("pending_todos") or []][:12],
                open_risks=[str(x) for x in result.get("open_risks") or []][:8],
                raw_recent=history[-keep:] if keep > 0 else [],
            )
        except Exception as exc:
            logger.warning("semantic context compress LLM failed: %s", exc)

    return _rule_based_summary(state, history)


def apply_semantic_context_compress(
    history: list[dict[str, Any]],
    *,
    state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Replace older messages with a semantic summary prefix + recent turns.
    Falls back to caller's char compression on failure.
    """
    summary = build_semantic_context_summary(history, state=state)
    if summary is None:
        return history
    prefix = summary.to_system_message()
    recent = summary.raw_recent or history[-settings.CONTEXT_COMPRESS_KEEP_RECENT_TURNS :]
    merged = [prefix] + [m for m in recent if m is not prefix]
    total = _history_char_count(merged)
    if total > settings.SESSION_MAX_HISTORY_CHARS and len(merged) > 2:
        merged = [prefix] + merged[-max(1, settings.CONTEXT_COMPRESS_KEEP_RECENT_TURNS) :]
    if merged is not history:
        record_context_compress_metrics(history, merged, method="semantic")
    return merged


def summary_as_dict(summary: SemanticContextSummary) -> dict[str, Any]:
    return asdict(summary)
