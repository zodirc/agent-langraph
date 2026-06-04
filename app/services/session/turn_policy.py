"""
Session turn policy — explicit state machine for mission resume vs QA isolation.

Industry pattern:
1. Active writing mission → default SUSPEND (no novel.txt leak on unrelated turns)
2. Fast path — explicit signals (continue, intervention, high-confidence config kinds)
3. Gray zone — lightweight LLM intent classifier
4. Audit — ``turn_policy_decision`` on payload for observability
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.services.manuscript_service import is_continue_writing_goal
from app.services.mission_intervention import intervention_from_payload
from app.services.mission_routing import explicit_mission_requested
from app.services.route_audit.config import RouteAuditConfig, load_route_audit_config
from app.services.route_audit.inference import infer_goal_kind_from_text
from app.services.session.config import SessionTurnPolicyConfig, load_session_turn_policy_config
from app.services.session.intent_classifier import classify_turn_intent_llm

TurnIntent = Literal["resume_mission", "isolate_qa"]


@dataclass(frozen=True)
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


def _matches_continue_patterns(goal: str, cfg: SessionTurnPolicyConfig) -> bool:
    text = (goal or "").strip()
    if not text:
        return False
    if is_continue_writing_goal(text):
        return True
    return any(p.search(text) for p in cfg.continue_goal_patterns)


def _pattern_kind_decision(
    goal: str,
    *,
    turn_cfg: SessionTurnPolicyConfig,
    route_cfg: RouteAuditConfig,
) -> TurnDecision | None:
    inference = infer_goal_kind_from_text(goal, cfg=route_cfg)
    primary = str(inference.get("primary_kind") or "general")
    confidence = float(inference.get("confidence") or 0.0)

    if confidence < turn_cfg.min_kind_confidence:
        return None

    if primary in turn_cfg.resume_on_kinds:
        return TurnDecision(
            intent="resume_mission",
            source="pattern_kind",
            kind=primary,
            confidence=confidence,
            reason=f"route_audit kind {primary}",
        )
    if primary in turn_cfg.isolate_on_kinds:
        return TurnDecision(
            intent="isolate_qa",
            source="pattern_kind",
            kind=primary,
            confidence=confidence,
            reason=f"route_audit kind {primary}",
        )
    return None


def _evaluate_active_mission_turn(
    goal: str,
    *,
    state: dict[str, Any],
    turn_cfg: SessionTurnPolicyConfig,
    route_cfg: RouteAuditConfig,
) -> TurnDecision:
    text = (goal or "").strip()

    if turn_cfg.isolate_when_empty_goal and not text:
        return TurnDecision(
            intent="isolate_qa",
            source="empty_goal",
            reason="empty goal while mission active",
        )

    if _matches_continue_patterns(text, turn_cfg):
        return TurnDecision(
            intent="resume_mission",
            source="continue_signal",
            reason="continue writing signal",
        )

    pattern = _pattern_kind_decision(text, turn_cfg=turn_cfg, route_cfg=route_cfg)
    if pattern is not None:
        return pattern

    llm = classify_turn_intent_llm(
        text,
        mission=state.get("mission") if isinstance(state.get("mission"), dict) else None,
        manuscript=state.get("manuscript") if isinstance(state.get("manuscript"), dict) else None,
        turn_cfg=turn_cfg,
        route_cfg=route_cfg,
        trace_state=state,
    )
    llm_intent = str(llm.get("turn_intent") or "qa_side_turn")
    llm_conf = float(llm.get("confidence") or 0.0)
    llm_source = str(llm.get("source") or "llm")
    llm_reason = str(llm.get("reason") or "")

    if llm_intent == "resume_writing":
        return TurnDecision(
            intent="resume_mission",
            source=llm_source,
            confidence=llm_conf,
            reason=llm_reason,
        )

    if turn_cfg.default_suspend_when_mission_active:
        return TurnDecision(
            intent="isolate_qa",
            source=llm_source if llm_source != "llm" else "default_suspend",
            confidence=llm_conf,
            reason=llm_reason or "default suspend while mission active",
        )

    return TurnDecision(
        intent="isolate_qa",
        source="fallback_isolate",
        reason="no resume signal",
    )


def resolve_session_turn(
    state: dict[str, Any],
    payload: dict[str, Any],
    goal: str,
    *,
    incoming: dict[str, Any] | None = None,
    turn_cfg: SessionTurnPolicyConfig | None = None,
    route_cfg: RouteAuditConfig | None = None,
) -> TurnDecision:
    """
    Main entry: decide resume_mission vs isolate_qa for this session turn.

    Call from ``prepare_session_turn`` before steer / enrich_payload.
    """
    turn_cfg = turn_cfg or load_session_turn_policy_config()
    route_cfg = route_cfg or load_route_audit_config()
    req = incoming if incoming is not None else payload

    if not turn_cfg.enabled:
        if explicit_mission_requested(req, str(req.get("execution_mode") or "")):
            return TurnDecision(
                intent="resume_mission",
                source="explicit_request",
                reason="policy disabled; explicit mission only",
            )
        return TurnDecision(
            intent="isolate_qa",
            source="policy_disabled",
            reason="session turn policy disabled",
        )

    if payload.get("mission_suspended"):
        return TurnDecision(
            intent="isolate_qa",
            source="already_suspended",
            reason="mission already suspended for this turn",
        )

    if explicit_mission_requested(req, str(req.get("execution_mode") or "")):
        return TurnDecision(
            intent="resume_mission",
            source="explicit_request",
            reason="explicit mission contract or execution_mode",
        )

    if intervention_from_payload(req):
        return TurnDecision(
            intent="resume_mission",
            source="intervention",
            reason="structured mission_intervention",
        )

    mission = state.get("mission")
    if not mission:
        archived = payload.get("archived_mission")
        if isinstance(archived, dict) and archived and _matches_continue_patterns(goal, turn_cfg):
            return TurnDecision(
                intent="resume_mission",
                source="continue_signal",
                reason="restore archived mission on continue",
            )
        return TurnDecision(
            intent="isolate_qa",
            source="no_mission",
            reason="no active or restorable mission",
        )

    return _evaluate_active_mission_turn(
        goal,
        state=state,
        turn_cfg=turn_cfg,
        route_cfg=route_cfg,
    )


def classify_turn_intent(
    goal: str,
    *,
    mission_active: bool = True,
    turn_cfg: SessionTurnPolicyConfig | None = None,
    route_cfg: RouteAuditConfig | None = None,
) -> TurnIntent:
    """Lightweight intent for tests; assumes active writing mission when ``mission_active``."""
    turn_cfg = turn_cfg or load_session_turn_policy_config()
    route_cfg = route_cfg or load_route_audit_config()
    stub_state: dict[str, Any] = (
        {"mission": {"kind": "writing", "objective": ""}} if mission_active else {}
    )
    decision = resolve_session_turn(
        stub_state,
        {"goal": goal},
        goal,
        incoming={"goal": goal},
        turn_cfg=turn_cfg,
        route_cfg=route_cfg,
    )
    return decision.intent


def is_ephemeral_qa_goal(goal: str) -> bool:
    """True when goal should not re-enter an active writing mission."""
    return classify_turn_intent(goal, mission_active=True) == "isolate_qa"


def should_enter_mission_runtime(
    state: dict[str, Any],
    payload: dict[str, Any],
    goal: str,
    *,
    incoming: dict[str, Any] | None = None,
) -> bool:
    """True when this turn should run mission graph (writing / orchestration)."""
    decision = resolve_session_turn(state, payload, goal, incoming=incoming)
    return decision.intent == "resume_mission"


def apply_qa_turn_isolation(payload: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    """Mark payload so this turn runs single-graph QA without mission writing."""
    out = dict(payload)
    out["execution_mode"] = "single"
    out["disable_mission_auto"] = True
    out["mission_suspended"] = True
    out.pop("mission", None)
    mission = existing.get("mission")
    if isinstance(mission, dict) and mission:
        out["archived_mission"] = mission
    wi = out.get("writing_intent")
    if isinstance(wi, dict):
        out["writing_intent"] = {**wi, "enabled": False, "source": "qa_turn_isolation"}
    else:
        out["writing_intent"] = {"enabled": False, "source": "qa_turn_isolation"}
    return out


def restore_archived_mission(state: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Re-attach mission from a prior QA-isolated turn when user continues writing."""
    archived = payload.get("archived_mission")
    if not isinstance(archived, dict) or not archived:
        lp = state.get("input_payload") or {}
        archived = lp.get("archived_mission")
    if not isinstance(archived, dict) or not archived:
        return payload
    out = dict(payload)
    out["mission"] = archived
    out["execution_mode"] = "mission"
    out.pop("mission_suspended", None)
    out.pop("disable_mission_auto", None)
    return out
