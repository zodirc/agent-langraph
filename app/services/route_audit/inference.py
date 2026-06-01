"""Score task kinds from configurable patterns + structural signals."""

from __future__ import annotations

from typing import Any

from app.runtime.state import AgentState
from app.services.route_audit.config import RouteAuditConfig, load_route_audit_config
from app.services.route_audit.signals import collect_signals


def _pattern_score(text: str, patterns: tuple) -> float:
    if not text or not patterns:
        return 0.0
    hits = sum(1 for p in patterns if p.search(text))
    if hits == 0:
        return 0.0
    return min(1.0, hits / max(1, len(patterns)) * 2.0)


def _structural_score(structural: dict[str, bool], required: frozenset[str]) -> float:
    if not required:
        return 0.0
    hits = sum(1 for key in required if structural.get(key))
    return hits / len(required)


def infer_task_kind(
    state: AgentState | dict[str, Any],
    *,
    cfg: RouteAuditConfig | None = None,
) -> dict[str, Any]:
    """
    Return primary kind, per-kind scores, and confidence.

    Kinds and patterns come from config — extend config.yaml, not Python branches.
    """
    cfg = cfg or load_route_audit_config()
    collected = collect_signals(state, cfg=cfg)
    text = str(collected.get("inference_text") or "")
    structural = dict(collected.get("structural") or {})

    scores: dict[str, float] = {}
    for rule in cfg.kinds:
        pat = _pattern_score(text, rule.patterns) * 0.6
        st = _structural_score(structural, rule.structural) * 0.4
        raw = (pat + st) * rule.weight
        if raw > 0:
            scores[rule.id] = round(raw, 4)

    if not scores:
        primary = "general"
        confidence = 0.0
    else:
        primary = max(scores, key=scores.get)  # type: ignore[arg-type]
        confidence = float(scores[primary])

    primary, confidence, switched_from = _auto_switch_primary_kind(
        primary=primary,
        confidence=confidence,
        scores=scores,
        structural=structural,
    )

    out = {
        "primary_kind": primary,
        "kind_scores": scores,
        "confidence": confidence,
        "structural": structural,
    }
    if switched_from:
        out["switched_from"] = switched_from
    return out


def infer_goal_kind_from_text(
    text: str,
    *,
    cfg: RouteAuditConfig | None = None,
) -> dict[str, Any]:
    """
    Pattern-only kind inference for a single user goal (no structural signals).

    Used by session turn policy so persisted mission state does not bias QA turns.
    """
    cfg = cfg or load_route_audit_config()
    goal = (text or "").strip()
    scores: dict[str, float] = {}
    for rule in cfg.kinds:
        pat = _pattern_score(goal, rule.patterns) * rule.weight
        if pat > 0:
            scores[rule.id] = round(pat, 4)

    if not scores:
        return {
            "primary_kind": "general",
            "kind_scores": {},
            "confidence": 0.0,
        }

    primary = max(scores, key=scores.get)  # type: ignore[arg-type]
    return {
        "primary_kind": primary,
        "kind_scores": scores,
        "confidence": float(scores[primary]),
    }


def _auto_switch_primary_kind(
    *,
    primary: str,
    confidence: float,
    scores: dict[str, float],
    structural: dict[str, bool],
) -> tuple[str, float, str | None]:
    """
    Dynamically promote mixed-scene turns to manuscript when writing evidence is strong.

    This avoids single-label lock-in where `qa` / `retry_recovery` suppress rewrite intents.
    """
    if primary not in ("qa", "retry_recovery"):
        return primary, confidence, None
    if structural.get("code_filename_in_tools"):
        return primary, confidence, None
    code_score = float(scores.get("code", 0.0))
    base_score = float(scores.get(primary, 0.0))
    if code_score >= max(0.3, base_score * 0.7):
        return primary, confidence, None

    writing_intent = bool(structural.get("writing_intent_enabled"))
    manuscript_anchor = bool(
        structural.get("manuscript_body_exists")
        or structural.get("manuscript_default_body")
        or structural.get("mission_writing")
    )
    if bool(structural.get("writing_tools_selected")) and not bool(
        structural.get("manuscript_body_exists")
    ):
        # Tool-only write intent without existing manuscript body is often code artifact flow.
        return primary, confidence, None
    if not (writing_intent and manuscript_anchor):
        return primary, confidence, None

    manuscript_score = float(scores.get("manuscript", 0.0))
    promoted = max(manuscript_score, max(0.45, base_score + 0.05))
    scores["manuscript"] = round(promoted, 4)
    return "manuscript", float(scores["manuscript"]), primary
