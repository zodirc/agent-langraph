"""Compare inferred task kind with the route planning selected."""

from __future__ import annotations

from typing import Any

from app.config.settings import settings
from app.runtime.state import AgentState
from app.services.route_audit.config import RouteAuditConfig, load_route_audit_config
from app.services.route_audit.inference import infer_task_kind

WRITING_TOOL_NAMES = frozenset({"write_text_artifact", "append_text_artifact"})


def _coerce_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def is_code_filename(name: str, cfg: RouteAuditConfig | None = None) -> bool:
    cfg = cfg or load_route_audit_config()
    lower = name.lower()
    return any(lower.endswith(ext) for ext in cfg.code_extensions)


def detect_planned_route(
    state: AgentState | dict[str, Any],
    *,
    cfg: RouteAuditConfig | None = None,
) -> str:
    cfg = cfg or load_route_audit_config()
    payload = state.get("input_payload") or {}
    if str(payload.get("target_mode") or "") == "engineering_mode":
        return "engineering_bounded"
    intent = _coerce_dict(payload.get("writing_intent"))
    tools = list(state.get("selected_tools") or [])

    if intent.get("enabled"):
        action = str(intent.get("action") or "")
        if action == "write_outline":
            return "writing_outline"
        tool_params = payload.get("tool_params") or {}
        param_blob = " ".join(str(k) + " " + str(v) for k, v in tool_params.items())
        wt_params = tool_params.get("write_text_artifact")
        planned_file = ""
        if isinstance(wt_params, dict):
            planned_file = str(wt_params.get("filename") or "")
        if is_code_filename(param_blob, cfg) or is_code_filename(planned_file, cfg):
            return "writing_code_artifact"
        return "writing_artifact"

    if any(t in WRITING_TOOL_NAMES for t in tools):
        return "writing_tools_only"

    if tools:
        return "tools_then_reasoning"
    return "reasoning_only"


def _resolve_artifact_profile(
    *,
    primary_kind: str,
    planned_route: str,
    cfg: RouteAuditConfig,
) -> str:
    if primary_kind == "code" or planned_route == "writing_code_artifact":
        return "source_code"
    if planned_route == "writing_outline":
        return "outline"
    if planned_route == "writing_artifact":
        return "manuscript_prose"
    return "plain_text"


def audit_planned_route(
    state: AgentState | dict[str, Any],
    *,
    cfg: RouteAuditConfig | None = None,
) -> dict[str, Any]:
    cfg = cfg or load_route_audit_config()
    inference = infer_task_kind(state, cfg=cfg)
    primary = str(inference.get("primary_kind") or "general")
    kind_scores = dict(inference.get("kind_scores") or {})
    confidence = float(inference.get("confidence") or 0.0)
    planned_route = detect_planned_route(state, cfg=cfg)

    issues: list[str] = []
    corrections: list[str] = []
    writing_blocked = False
    force_slow_reasoning = False

    for rule in cfg.conflicts:
        if primary != rule.when_kind:
            continue
        if rule.min_kind_score is not None and confidence < rule.min_kind_score:
            continue
        if planned_route != rule.planned_route:
            continue
        if _should_allow_mixed_qa_writing(
            primary=primary,
            planned_route=planned_route,
            inference=inference,
            state=state,
        ):
            continue
        if rule.unless_kind and float(kind_scores.get(rule.unless_kind, 0)) >= cfg.min_kind_score:
            if kind_scores.get(rule.unless_kind, 0) >= kind_scores.get(primary, 0):
                continue

        issues.append(
            f"task_kind={primary} conflicts with planned_route={planned_route} "
            f"(rule action={rule.action})"
        )
        if rule.action == "disable_writing_force_reasoning":
            corrections.extend(["disable_writing_intent", "force_slow_reasoning"])
            from app.services.delivery_policy import should_strip_writing_tools

            if should_strip_writing_tools(
                planned_route,
                inferred_kind=primary,
            ):
                corrections.append("strip_writing_tools")
            else:
                corrections.append("rewrite_tool_filenames")
            writing_blocked = True
            force_slow_reasoning = True
        elif rule.action == "disable_writing":
            corrections.extend(["disable_writing_intent", "strip_writing_tools"])
            writing_blocked = True

    issues.extend(_writing_delivery_route_issues(state, planned_route=planned_route))
    aligned = not issues
    artifact_profile = _resolve_artifact_profile(
        primary_kind=primary,
        planned_route=planned_route,
        cfg=cfg,
    )

    return {
        "enabled": cfg.enabled,
        "inferred_kind": primary,
        "kind_scores": kind_scores,
        "kind_confidence": confidence,
        "planned_route": planned_route,
        "aligned": aligned,
        "issues": issues,
        "corrections": list(dict.fromkeys(corrections)),
        "writing_blocked": writing_blocked,
        "force_slow_reasoning": force_slow_reasoning,
        "artifact_profile": artifact_profile,
        "structural": inference.get("structural"),
    }


def _writing_delivery_route_issues(
    state: AgentState | dict[str, Any],
    *,
    planned_route: str,
) -> list[str]:
    """Flag answer-only plans when writing delivery or operator requires artifact writes."""
    payload = state.get("input_payload") or {}
    intent = _coerce_dict(payload.get("writing_intent"))
    if not intent.get("enabled"):
        return []
    tools = list(state.get("selected_tools") or [])
    if tools:
        return []
    actions = [a for a in (state.get("planned_actions") or []) if isinstance(a, dict)]
    if any(str(a.get("type") or "") in ("write_artifact", "edit_artifact", "run_tool") for a in actions):
        return []
    goal = str(payload.get("goal") or payload.get("query") or "").strip()
    from app.services.writing_intent_classifier import classify_writing_operator, goal_is_writing_manuscript_action

    operator = str(payload.get("writing_operator") or "") or (
        classify_writing_operator(goal, state) or ""
    )
    if payload.get("interaction_goal") == "delivery" or operator or goal_is_writing_manuscript_action(
        goal, state
    ):
        return [
            "writing_delivery_requires_tools: planned_route=reasoning_only "
            f"but writing_intent enabled (operator={operator or 'delivery'})"
        ]
    return []


def _should_allow_mixed_qa_writing(
    *,
    primary: str,
    planned_route: str,
    inference: dict[str, Any],
    state: AgentState | dict[str, Any],
) -> bool:
    """
    Allow QA turns with strong manuscript evidence to execute writing routes.

    This preserves multi-scene conversations: a turn can still be broadly QA while
    carrying a concrete "rewrite existing manuscript" sub-intent.
    """
    if primary != "qa":
        return False
    if planned_route != "writing_artifact":
        return False
    structural = dict(inference.get("structural") or {})
    payload = state.get("input_payload") or {}
    intent = _coerce_dict(payload.get("writing_intent"))
    has_manuscript_signal = bool(
        structural.get("manuscript_body_exists") or structural.get("manuscript_default_body")
    )
    if not has_manuscript_signal:
        return False
    if bool(intent.get("enabled")):
        return True
    kind_scores = dict(inference.get("kind_scores") or {})
    manuscript_score = float(kind_scores.get("manuscript", 0.0))
    qa_score = float(kind_scores.get("qa", 0.0))
    return manuscript_score >= max(0.25, qa_score * 0.4)
