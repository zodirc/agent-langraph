"""Structural signals for task-kind inference (state-derived, not goal keywords)."""

from __future__ import annotations

from typing import Any

from app.config.settings import settings
from app.runtime.state import AgentState
from app.services.artifact_content import parse_requested_chars
from app.services.conversation_context import (
    build_session_outcomes_digest,
    conversation_history_for_llm,
    conversation_history_from_state,
)
from app.services.route_audit.config import RouteAuditConfig, load_route_audit_config

WRITING_TOOL_NAMES = frozenset({"write_text_artifact", "append_text_artifact"})


def _collect_inference_text(state: AgentState | dict[str, Any]) -> str:
    payload = state.get("input_payload") or {}
    parts: list[str] = [str(payload.get("goal") or payload.get("query") or "")]
    history = conversation_history_for_llm(conversation_history_from_state(state))
    for msg in history[-6:]:
        if str(msg.get("role")) == "user":
            parts.append(str(msg.get("content") or ""))
    digest = build_session_outcomes_digest(state)
    if digest:
        parts.append(digest)
    return "\n".join(p for p in parts if p.strip())


def _code_extension_hit(text: str, cfg: RouteAuditConfig) -> bool:
    lower = text.lower()
    return any(ext in lower for ext in cfg.code_extensions)


def collect_structural_signals(
    state: AgentState | dict[str, Any],
    *,
    cfg: RouteAuditConfig | None = None,
) -> dict[str, bool]:
    cfg = cfg or load_route_audit_config()
    payload = state.get("input_payload") or {}
    intent = payload.get("writing_intent") or {}
    tools = list(state.get("selected_tools") or [])
    tool_params = payload.get("tool_params") or {}

    task_id = str(state.get("task_id") or "")
    has_body = False
    body_name = ""
    if task_id:
        from app.services.artifact_resolver import build_artifact_manifest

        entries = build_artifact_manifest(task_id)
        prose = [e for e in entries if e.filename.lower().endswith((".txt", ".md")) and e.bytes > 0]
        has_body = bool(prose)
        if prose:
            body_name = prose[0].filename.lower()

    outcomes = list(payload.get("session_outcomes") or [])
    recent_rejected = any(
        str(o.get("outcome")) in ("rejected", "failed")
        for o in outcomes[-4:]
    )

    goal = str(payload.get("goal") or "")
    requested = parse_requested_chars(goal) or 0
    long_form = requested >= int(getattr(settings, "MISSION_ORCHESTRATION_MIN_CHARS", 8000) // 2)

    params_blob = str(tool_params)
    plan_blob = " ".join(str(s) for s in (state.get("plan") or []))

    return {
        "writing_intent_enabled": bool(intent.get("enabled")),
        "mission_writing": False,
        "mission_runtime": False,
        "writing_tools_selected": any(t in WRITING_TOOL_NAMES for t in tools),
        "manuscript_body_exists": has_body,
        "manuscript_default_body": body_name in cfg.manuscript_body_names,
        "session_outcomes_rejected_recent": recent_rejected,
        "code_filename_in_tools": _code_extension_hit(params_blob + plan_blob, cfg),
        "makefile_in_tools": "makefile" in (params_blob + plan_blob).lower(),
        "long_form_chars_requested": long_form,
        "planning_skip_retrieval_no_tools": bool(state.get("skip_retrieval"))
        and not tools,
    }


def collect_signals(
    state: AgentState | dict[str, Any],
    *,
    cfg: RouteAuditConfig | None = None,
) -> dict[str, Any]:
    cfg = cfg or load_route_audit_config()
    text = _collect_inference_text(state)
    structural = collect_structural_signals(state, cfg=cfg)
    return {
        "inference_text": text,
        "structural": structural,
    }
