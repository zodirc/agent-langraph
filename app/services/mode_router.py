"""Intent → target_mode resolution and session mode switching."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from app.runtime.state import AgentState
from app.services.mode_registry import ModeContract, get_mode_contract
from app.services.route_audit.config import RouteAuditConfig, load_route_audit_config
from app.services.route_audit.inference import infer_goal_kind_from_text, infer_task_kind

ModeSwitchAction = Literal["stay", "switch", "isolate"]

_ENGINEERING_INTENTS = frozenset({"interactive_app", "small_project", "code"})
_QA_LIKE_INTENTS = frozenset({"qa", "general", "retry_recovery"})

_QA_FOLLOWUP_RE = re.compile(
    r"(?i)(为什么|为何|怎么.*设计|原理|解释一下|what\s+if|why\s+does|how\s+does\s+it\s+work)",
)
_EXPLICIT_DELIVERY_RE = re.compile(
    r"(?i)(请直接|帮我).*(生成|写出|落盘).*(项目|文件|源码|程序|游戏|demo)",
)


def refine_mode_for_session_switch(
    *,
    intent_kind: str,
    target_mode: str,
    current_mode: str | None,
    confidence: float,
    goal: str,
    min_kind_score: float,
) -> tuple[str, str, str]:
    """
    §10.2 explicit session rules on top of config mapping.
    Returns (intent_kind, target_mode, reason_suffix).
    """
    text = (goal or "").strip()
    reason = ""

    if current_mode == "engineering_mode":
        from app.services.interaction_goal import goal_is_conversational_qa

        if goal_is_conversational_qa(text):
            return "qa", "qa_mode", "engineering_to_conversational_qa"
        qa_hit = intent_kind in _QA_LIKE_INTENTS and confidence >= min_kind_score
        if qa_hit or _QA_FOLLOWUP_RE.search(text):
            return intent_kind, "qa_mode", "engineering_to_qa_followup"

    if current_mode in (None, "qa_mode") and intent_kind in _ENGINEERING_INTENTS:
        if _EXPLICIT_DELIVERY_RE.search(text) or confidence >= min_kind_score:
            return intent_kind, "engineering_mode", "qa_to_engineering_explicit_delivery"

    return intent_kind, target_mode, reason


@dataclass(frozen=True)
class ModeResolution:
    intent_kind: str
    target_mode: str
    confidence: float
    current_mode: str | None
    mode_switch_action: ModeSwitchAction
    mode_switch_reason: str
    contract: ModeContract | None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "intent_kind": self.intent_kind,
            "target_mode": self.target_mode,
            "confidence": round(self.confidence, 4),
            "current_mode": self.current_mode,
            "mode_switch_action": self.mode_switch_action,
            "mode_switch_reason": self.mode_switch_reason,
        }
        if self.contract is not None:
            from app.services.mode_registry import contract_to_trace_dict

            out["effective_mode_contract"] = contract_to_trace_dict(self.contract)
        return out


def load_mode_routing_map() -> dict[str, str]:
    from app.config.settings import settings

    cfg = getattr(settings, "MODE_ROUTING_CONFIG", None)
    if not isinstance(cfg, dict):
        return {}
    by_intent = cfg.get("by_intent") or {}
    if not isinstance(by_intent, dict):
        return {}
    return {str(k): str(v) for k, v in by_intent.items()}


def map_intent_to_mode(intent_kind: str) -> str:
    routing = load_mode_routing_map()
    kind = (intent_kind or "general").strip().lower()
    return routing.get(kind) or routing.get("general") or "qa_mode"


def normalize_intent_kind(raw_kind: str) -> str:
    kind = (raw_kind or "general").strip().lower()
    if kind in load_mode_routing_map():
        return kind
    if kind in _ENGINEERING_INTENTS:
        return kind
    if kind == "manuscript":
        return "manuscript"
    if kind in _QA_LIKE_INTENTS:
        return "qa" if kind == "general" else kind
    return "general"


def infer_intent_kind(
    state: AgentState | dict[str, Any],
    *,
    route_cfg: RouteAuditConfig | None = None,
) -> tuple[str, float]:
    """Prefer post-planning route_audit; fall back to goal text patterns."""
    payload = state.get("input_payload") or {}
    audit = payload.get("route_audit") or {}
    if audit.get("inferred_kind"):
        return (
            normalize_intent_kind(str(audit.get("inferred_kind"))),
            float(audit.get("kind_confidence") or 0.0),
        )
    goal = str(payload.get("goal") or payload.get("query") or "")
    route_cfg = route_cfg or load_route_audit_config()
    inferred = infer_task_kind(state, cfg=route_cfg)
    if float(inferred.get("confidence") or 0.0) >= route_cfg.min_kind_score:
        return (
            normalize_intent_kind(str(inferred.get("primary_kind"))),
            float(inferred.get("confidence") or 0.0),
        )
    text_inf = infer_goal_kind_from_text(goal, cfg=route_cfg)
    return (
        normalize_intent_kind(str(text_inf.get("primary_kind"))),
        float(text_inf.get("confidence") or 0.0),
    )


def _resolve_switch_action(
    *,
    current_mode: str | None,
    target_mode: str,
    contract: ModeContract | None,
) -> tuple[ModeSwitchAction, str]:
    if not current_mode:
        return "switch", "initial_mode_selection"
    if current_mode == target_mode:
        return "stay", "mode_unchanged"
    if contract and current_mode in contract.guards.force_isolate_from:
        return "isolate", f"isolate_from_{current_mode}_to_{target_mode}"
    return "switch", f"mode_change_{current_mode}_to_{target_mode}"


def resolve_target_mode(
    state: AgentState | dict[str, Any],
    *,
    route_cfg: RouteAuditConfig | None = None,
) -> ModeResolution:
    route_cfg = route_cfg or load_route_audit_config()
    intent_kind, confidence = infer_intent_kind(state, route_cfg=route_cfg)
    target_mode = map_intent_to_mode(intent_kind)
    payload = state.get("input_payload") or {}
    goal = str(payload.get("goal") or "")
    current_mode = str(payload.get("current_mode") or payload.get("target_mode") or "").strip() or None
    intent_kind, target_mode, switch_note = refine_mode_for_session_switch(
        intent_kind=intent_kind,
        target_mode=target_mode,
        current_mode=current_mode,
        confidence=confidence,
        goal=goal,
        min_kind_score=route_cfg.min_kind_score,
    )
    contract = get_mode_contract(target_mode)
    action, reason = _resolve_switch_action(
        current_mode=current_mode,
        target_mode=target_mode,
        contract=contract,
    )
    if switch_note:
        reason = f"{reason};{switch_note}"
    return ModeResolution(
        intent_kind=intent_kind,
        target_mode=target_mode,
        confidence=confidence,
        current_mode=current_mode,
        mode_switch_action=action,
        mode_switch_reason=reason,
        contract=contract,
    )


def is_engineering_mode(resolution: ModeResolution | dict[str, Any]) -> bool:
    if isinstance(resolution, dict):
        return str(resolution.get("target_mode") or "") == "engineering_mode"
    return resolution.target_mode == "engineering_mode"


def apply_engineering_turn_isolation(payload: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    """Isolate active manuscript/mission context before engineering delivery."""
    from app.services.session.turn_policy import apply_qa_turn_isolation

    out = apply_qa_turn_isolation(payload, existing)
    out["engineering_turn"] = True
    out["mode_isolated"] = True
    wi = out.get("writing_intent")
    if isinstance(wi, dict):
        out["writing_intent"] = {**wi, "enabled": False, "source": "engineering_isolation"}
    return out
