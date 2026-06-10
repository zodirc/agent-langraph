"""Apply route audit corrections to state before execution."""

from __future__ import annotations

from typing import Any

from app.runtime.state import AgentState, append_audit, merge_state
from app.services.delivery_policy import (
    load_delivery_config,
    resolve_delivery_plan,
    rewrite_manuscript_filenames_for_code,
)
from app.services.route_audit.audit import WRITING_TOOL_NAMES
from app.services.route_audit.config import load_route_audit_config


def writing_gate_allowed(state: AgentState | dict[str, Any]) -> bool:
    cfg = load_route_audit_config()
    if not cfg.enabled:
        payload = state.get("input_payload") or {}
        return bool((payload.get("writing_intent") or {}).get("enabled"))
    payload = state.get("input_payload") or {}
    audit = payload.get("route_audit") or {}
    if audit.get("writing_blocked"):
        return False
    return bool((payload.get("writing_intent") or {}).get("enabled"))


def _attach_delivery_metadata(
    state: AgentState,
    audit: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    delivery = resolve_delivery_plan(state, audit)
    payload["delivery_plan"] = delivery
    profile = audit.get("artifact_profile") or delivery.get("artifact_profile")
    if profile:
        payload["artifact_profile"] = profile
    payload["route_audit"] = audit
    return payload


def apply_route_corrections(
    state: AgentState,
    audit: dict[str, Any],
) -> AgentState:
    """Mutate payload/tools per audit; idempotent for a given audit dict."""
    if not audit:
        return state

    payload = dict(state.get("input_payload") or {})

    if audit.get("aligned", True):
        payload = _attach_delivery_metadata(state, audit, payload)
        return merge_state(state, input_payload=payload)

    corrections = list(audit.get("corrections") or [])
    if not corrections:
        payload = _attach_delivery_metadata(state, audit, payload)
        return merge_state(state, input_payload=payload)

    raw_intent = payload.get("writing_intent")
    intent = dict(raw_intent) if isinstance(raw_intent, dict) else {}
    tools = list(state.get("selected_tools") or [])

    if "disable_writing_intent" in corrections:
        intent = {
            **intent,
            "enabled": False,
            "blocked_by": "route_audit",
            "blocked_route": audit.get("planned_route"),
            "inferred_kind": audit.get("inferred_kind"),
        }
        payload["writing_intent"] = intent

    if "strip_writing_tools" in corrections:
        tools = [t for t in tools if t not in WRITING_TOOL_NAMES]
        payload["tool_params"] = {
            k: v
            for k, v in (payload.get("tool_params") or {}).items()
            if k not in WRITING_TOOL_NAMES
        }

    if "rewrite_tool_filenames" in corrections:
        delivery_cfg = load_delivery_config()
        kind_plan = delivery_cfg.kind_plan(str(audit.get("inferred_kind") or ""))
        ext = kind_plan.default_extension if kind_plan else ".cpp"
        payload, _ = rewrite_manuscript_filenames_for_code(
            merge_state(state, input_payload=payload),
            extension=ext,
        )

    if "force_slow_reasoning" in corrections:
        payload["force_slow_reasoning"] = True
        payload["skip_reasoning_after_tools"] = False

    payload = _attach_delivery_metadata(state, audit, payload)
    delivery = payload.get("delivery_plan") or {}

    updated = merge_state(
        state,
        input_payload=payload,
        selected_tools=tools,
        audit_log=append_audit(
            state,
            "route_audit",
            "corrected",
            {
                "issues": audit.get("issues"),
                "corrections": corrections,
                "planned_route": audit.get("planned_route"),
                "inferred_kind": audit.get("inferred_kind"),
                "delivery_plan": delivery,
            },
        ),
    )
    return updated


def build_replan_feedback(audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "aligned": audit.get("aligned"),
        "issues": audit.get("issues"),
        "inferred_kind": audit.get("inferred_kind"),
        "planned_route": audit.get("planned_route"),
        "required_corrections": audit.get("corrections"),
        "hint": (
            "Re-plan: match execution path to inferred task kind. "
            "Code tasks → writing_intent.enabled=false, use reasoning structured.artifacts "
            "or write_text_artifact with a .cpp/.py filename (tool_execution, not novel gateway). "
            "Long-form fiction → writing_intent or mission, not novel gateway for code."
        ),
    }
