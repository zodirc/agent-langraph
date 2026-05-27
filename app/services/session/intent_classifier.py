"""LLM + local fallback classifier for mission-active turn intent (gray zone)."""

from __future__ import annotations

import json
from typing import Any, Literal

from app.services.llm_client import invoke_structured
from app.services.route_audit.config import RouteAuditConfig, load_route_audit_config
from app.services.route_audit.inference import infer_goal_kind_from_text
from app.services.session.config import SessionTurnPolicyConfig, load_session_turn_policy_config

TurnIntentLabel = Literal["resume_writing", "qa_side_turn"]

_SYSTEM = """You classify a user message during an active long-form writing mission.

Output ONE JSON object:
- turn_intent: "resume_writing" | "qa_side_turn"
- confidence: number 0.0-1.0
- reason: short string

resume_writing — continue, steer, edit outline/body/plot, review manuscript, or otherwise
  advance the writing project.

qa_side_turn — greeting, unrelated knowledge question, small talk, or anything that must NOT
  open the writing pipeline or stream manuscript deltas.

When uncertain, choose qa_side_turn (safe default). Mixed messages (greeting + writing steer)
  should be resume_writing only if writing intent is clear."""


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit]


def _local_pattern_fallback(
    goal: str,
    *,
    turn_cfg: SessionTurnPolicyConfig,
    route_cfg: RouteAuditConfig,
) -> dict[str, Any]:
    inference = infer_goal_kind_from_text(goal, cfg=route_cfg)
    primary = str(inference.get("primary_kind") or "general")
    confidence = float(inference.get("confidence") or 0.0)

    if confidence >= turn_cfg.min_kind_confidence:
        if primary in turn_cfg.resume_on_kinds:
            return {
                "turn_intent": "resume_writing",
                "confidence": confidence,
                "reason": f"pattern kind {primary}",
                "source": "pattern_fallback",
            }
        if primary in turn_cfg.isolate_on_kinds:
            return {
                "turn_intent": "qa_side_turn",
                "confidence": confidence,
                "reason": f"pattern kind {primary}",
                "source": "pattern_fallback",
            }

    return {
        "turn_intent": "qa_side_turn",
        "confidence": 0.0,
        "reason": "default suspend (no high-confidence resume signal)",
        "source": "default_suspend",
    }


def classify_turn_intent_llm(
    goal: str,
    *,
    mission: dict[str, Any] | None = None,
    manuscript: dict[str, Any] | None = None,
    turn_cfg: SessionTurnPolicyConfig | None = None,
    route_cfg: RouteAuditConfig | None = None,
) -> dict[str, Any]:
    """
    Classify gray-zone goals while a writing mission is active.

    Returns dict with turn_intent, confidence, reason, source.
    """
    turn_cfg = turn_cfg or load_session_turn_policy_config()
    route_cfg = route_cfg or load_route_audit_config()
    text = _clip(goal, turn_cfg.llm_intent_max_goal_chars)

    if not text:
        return {
            "turn_intent": "qa_side_turn",
            "confidence": 1.0,
            "reason": "empty goal",
            "source": "empty_goal",
        }

    if not turn_cfg.llm_intent_enabled:
        return _local_pattern_fallback(text, turn_cfg=turn_cfg, route_cfg=route_cfg)

    payload = {
        "user_message": text,
        "mission_objective": _clip(str((mission or {}).get("objective") or ""), 500),
        "mission_kind": str((mission or {}).get("kind") or ""),
        "has_manuscript_body": bool((manuscript or {}).get("body_bytes")),
        "body_path": str((manuscript or {}).get("body_path") or ""),
    }

    try:
        raw = invoke_structured(
            "session_turn",
            _SYSTEM,
            json.dumps(payload, ensure_ascii=False),
        )
    except Exception:
        return _local_pattern_fallback(text, turn_cfg=turn_cfg, route_cfg=route_cfg)

    intent = str(raw.get("turn_intent") or "").strip()
    confidence = float(raw.get("confidence") or 0.0)
    reason = str(raw.get("reason") or "").strip()

    if intent not in ("resume_writing", "qa_side_turn"):
        return _local_pattern_fallback(text, turn_cfg=turn_cfg, route_cfg=route_cfg)

    if confidence < turn_cfg.llm_intent_min_confidence:
        return _local_pattern_fallback(text, turn_cfg=turn_cfg, route_cfg=route_cfg)

    return {
        "turn_intent": intent,
        "confidence": confidence,
        "reason": reason or f"llm {intent}",
        "source": "llm",
    }
