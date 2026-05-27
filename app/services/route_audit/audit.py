"""Compare inferred task kind with the route planning selected."""

from __future__ import annotations

from typing import Any

from app.config.settings import settings
from app.runtime.state import AgentState
from app.services.manuscript_service import WRITING_TOOL_NAMES, resolve_manuscript
from app.services.mission_schema import should_use_mission_runtime
from app.services.route_audit.config import RouteAuditConfig, load_route_audit_config
from app.services.route_audit.inference import infer_task_kind


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
    intent = payload.get("writing_intent") or {}
    mission = payload.get("mission") or state.get("mission") or {}
    tools = list(state.get("selected_tools") or [])

    if should_use_mission_runtime(payload, str(state.get("execution_mode") or "")):
        if str(mission.get("kind") or "").lower() == "writing":
            return "mission_writing"

    if intent.get("enabled"):
        action = str(intent.get("action") or "")
        if action == "write_outline":
            return "writing_outline"
        task_id = str(state.get("task_id") or "")
        ms = resolve_manuscript(task_id, state.get("manuscript")) if task_id else None
        default_body = str(getattr(settings, "MANUSCRIPT_DEFAULT_BODY", "novel.txt"))
        body = (ms.body_path if ms else None) or default_body
        tool_params = payload.get("tool_params") or {}
        param_blob = " ".join(str(k) + " " + str(v) for k, v in tool_params.items())
        wt_params = tool_params.get("write_text_artifact")
        planned_file = ""
        if isinstance(wt_params, dict):
            planned_file = str(wt_params.get("filename") or "")
        if (
            is_code_filename(body, cfg)
            or is_code_filename(param_blob, cfg)
            or is_code_filename(planned_file, cfg)
        ):
            return "writing_code_artifact"
        if body.lower() in cfg.manuscript_body_names:
            return "writing_manuscript"
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
    if planned_route in ("writing_manuscript", "mission_writing", "writing_artifact"):
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
