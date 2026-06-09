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

from app.runtime.state import TaskStatus
from app.services.manuscript_service import is_continue_writing_goal
from app.services.mission_intervention import intervention_from_payload
from app.services.mission_routing import explicit_mission_requested
from app.services.route_audit.config import RouteAuditConfig, load_route_audit_config
from app.services.route_audit.inference import infer_goal_kind_from_text
from app.services.session.config import SessionTurnPolicyConfig, load_session_turn_policy_config
from app.services.session.intent_classifier import classify_turn_intent_llm

TurnIntent = Literal["resume_mission", "supersede_active_mission", "isolate_qa"]


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


def failed_mission_steer_correction(
    state: dict[str, Any],
    payload: dict[str, Any],
    goal: str,
) -> TurnDecision | None:
    """FAILED turn + active mission + steer correction → replan, not checkpoint resume."""
    text = (goal or "").strip()
    if not text or not _goal_requires_steer_replan(text):
        return None
    status = str(state.get("status") or "")
    if not (
        status.endswith("FAILED")
        or status in (TaskStatus.FAILED.value, TaskStatus.TOOL_FAILED.value)
    ):
        return None
    has_mission = _active_mission_block(state, payload) is not None
    if not has_mission and not explicit_mission_requested(
        payload,
        str(payload.get("execution_mode") or state.get("execution_mode") or ""),
    ):
        return None
    return TurnDecision(
        intent="supersede_active_mission",
        source="failed_steer_correction",
        reason="failed mission steer requires replan (not resume)",
    )


def completed_mission_steer_correction(
    state: dict[str, Any],
    payload: dict[str, Any],
    goal: str,
) -> TurnDecision | None:
    """COMPLETED session + active mission + mid-mission correction → replan, not resume."""
    mission = _active_mission_block(state, payload)
    text = (goal or "").strip()
    if mission is None or not text:
        return None
    if str(state.get("status") or "") != TaskStatus.COMPLETED.value:
        return None
    if not _goal_requires_steer_replan(text):
        return None
    return TurnDecision(
        intent="supersede_active_mission",
        source="completed_steer_correction",
        reason="completed mission correction requires replan (not resume)",
    )


def _goal_requires_steer_replan(goal: str) -> bool:
    """True for mid-mission direction corrections that need replan, not casual QA."""
    import re

    from app.services.interaction_goal import goal_is_mission_status_query
    from app.services.manuscript_service import is_continue_writing_goal

    text = (goal or "").strip()
    if not text or is_continue_writing_goal(text) or goal_is_mission_status_query(text):
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


def _coerce_mission_dict(raw: Any) -> dict[str, Any] | None:
    return dict(raw) if isinstance(raw, dict) and raw else None


def _active_mission_block(
    state: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Resolve non-suspended mission from session state or merged payload."""
    mission = None
    for source in (
        state.get("mission"),
        payload.get("mission"),
        (state.get("input_payload") or {}).get("mission"),
    ):
        mission = _coerce_mission_dict(source)
        if mission is not None:
            break
    if mission is None:
        return None
    if payload.get("mission_suspended"):
        return None
    return mission


def _mission_session_ongoing(state: dict[str, Any]) -> bool:
    """True when a mission turn is already in flight (not a fresh explicit contract)."""
    if int(state.get("session_turn") or 0) > 1:
        return True
    status = str(state.get("status") or "")
    return status in (
        "MISSION_RUNNING",
        "MISSION_PAUSED",
        "PLANNED",
        "POLICY_CHECKED",
        "REASONED",
        "WAITING_REVIEW",
        "WRITING",
    )


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

    from app.services.interaction_goal import goal_is_mission_status_query

    if goal_is_mission_status_query(text):
        return TurnDecision(
            intent="isolate_qa",
            source="status_query",
            reason="mission status/meta question — do not append chapter",
        )

    if _matches_continue_patterns(text, turn_cfg):
        return TurnDecision(
            intent="resume_mission",
            source="continue_signal",
            reason="continue writing signal",
        )

    if _goal_requires_steer_replan(text):
        return TurnDecision(
            intent="supersede_active_mission",
            source="steer_correction",
            reason="mid-mission correction requires replan (not mechanical append)",
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


def _is_user_resend(req: dict[str, Any], payload: dict[str, Any]) -> bool:
    for block in (req, payload):
        if block.get("resend") is True:
            return True
        meta = block.get("meta")
        if isinstance(meta, dict) and meta.get("resend"):
            return True
    return False


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

    if _is_user_resend(req, payload):
        mission = state.get("mission")
        if not isinstance(mission, dict) or not mission:
            mission = payload.get("mission") or (state.get("input_payload") or {}).get("mission")
        if isinstance(mission, dict) and mission and not payload.get("mission_suspended"):
            return TurnDecision(
                intent="supersede_active_mission",
                source="user_resend",
                reason="resend retries goal with active mission — replan, not resume",
            )
        return TurnDecision(
            intent="isolate_qa",
            source="user_resend",
            reason="resend retries turn without resume",
        )

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

    completed_correction = completed_mission_steer_correction(state, payload, goal)
    if completed_correction is not None:
        return completed_correction

    failed_correction = failed_mission_steer_correction(state, payload, goal)
    if failed_correction is not None:
        return failed_correction

    mission = _active_mission_block(state, payload)
    if mission is not None and _mission_session_ongoing(state) and (goal or "").strip():
        eval_state = state if isinstance(state.get("mission"), dict) and state.get("mission") else {
            **state,
            "mission": mission,
        }
        return _evaluate_active_mission_turn(
            goal,
            state=eval_state,
            turn_cfg=turn_cfg,
            route_cfg=route_cfg,
        )

    if explicit_mission_requested(req, str(req.get("execution_mode") or "")):
        # Stale mission block in payload must not block engineering / QA delivery turns.
        pattern = _pattern_kind_decision(goal, turn_cfg=turn_cfg, route_cfg=route_cfg)
        if pattern is not None and pattern.intent == "isolate_qa":
            return pattern
        if (
            _active_mission_block(state, payload) is not None
            and str(state.get("status") or "") == TaskStatus.COMPLETED.value
            and (goal or "").strip()
            and _goal_requires_steer_replan(goal)
        ):
            return TurnDecision(
                intent="supersede_active_mission",
                source="completed_explicit_mission_steer",
                reason="completed mission with steer correction — replan not resume",
            )
        return TurnDecision(
            intent="resume_mission",
            source="explicit_request",
            reason="explicit mission contract or execution_mode",
        )

    if intervention_from_payload(req):
        iv = intervention_from_payload(req) or {}
        action = str(iv.get("action") or "")
        supersede_actions = frozenset(
            {
                "rewrite_outline",
                "reset_body",
                "edit_plot",
                "review_outline",
                "batch_unit_quality",
            }
        )
        if action in supersede_actions:
            return TurnDecision(
                intent="supersede_active_mission",
                source="intervention",
                reason=f"corrective intervention {action}",
            )
        return TurnDecision(
            intent="resume_mission",
            source="intervention",
            reason="structured mission_intervention",
        )

    mission = state.get("mission")
    if not isinstance(mission, dict) or not mission:
        mission = (payload.get("mission") or (state.get("input_payload") or {}).get("mission"))
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
    return decision.intent in ("resume_mission", "supersede_active_mission")


def apply_qa_turn_isolation(payload: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    """Mark payload so this turn runs single-graph QA without mission writing."""
    from app.services.manuscript_service import sanitize_manuscript_bindings

    out = dict(payload)
    out["execution_mode"] = "single"
    out["disable_mission_auto"] = True
    out["mission_suspended"] = True
    out.pop("mission", None)
    mission = existing.get("mission")
    if isinstance(mission, dict) and mission:
        out["archived_mission"] = mission
    for key in ("manuscript",):
        raw = out.get(key) or existing.get(key) or (existing.get("input_payload") or {}).get(
            key
        )
        if raw:
            out[key] = sanitize_manuscript_bindings(raw)
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
    out.pop("suspension_reason", None)
    out["draft_mission_state"] = "active"
    wi = out.get("writing_intent")
    if isinstance(wi, dict):
        out["writing_intent"] = {**wi, "enabled": True}
    return out


def apply_revision_turn_isolation(payload: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    """Suspend draft/unit-loop mission while user performs local revision."""
    from app.services.manuscript_service import sanitize_manuscript_bindings

    out = dict(payload)
    out["mission_suspended"] = True
    out["suspension_reason"] = "user_revision_override"
    out["draft_mission_state"] = "suspended"
    mission = existing.get("mission") or out.get("mission")
    if isinstance(mission, dict) and mission:
        out["archived_mission"] = mission
        out.pop("mission", None)
    for key in ("manuscript",):
        raw = out.get(key) or existing.get(key) or (existing.get("input_payload") or {}).get(key)
        if raw:
            out[key] = sanitize_manuscript_bindings(raw)
    wi = out.get("writing_intent")
    if isinstance(wi, dict):
        out["writing_intent"] = {**wi, "enabled": False, "source": "revision_turn_isolation"}
    else:
        out["writing_intent"] = {"enabled": False, "source": "revision_turn_isolation"}
    return out
