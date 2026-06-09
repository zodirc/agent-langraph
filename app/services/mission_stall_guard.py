"""Oscillation / no-progress guard for mission control (optimization.md §3.2)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.config.settings import settings
from app.runtime.state import AgentState


def _anchor_hash(payload: dict[str, Any]) -> str:
    anchor = (payload.get("mission_intervention") or {}).get("intent_anchor") or {}
    if not isinstance(anchor, dict):
        anchor = {}
    wi = payload.get("writing_intent") or {}
    cmd = payload.get("writing_command") or {}
    edit_spec = cmd.get("edit_spec") if isinstance(cmd, dict) else {}
    blob = json.dumps(
        {
            "anchor": anchor,
            "edit_spec": edit_spec if isinstance(edit_spec, dict) else {},
            "reason": (payload.get("mission_intervention") or {}).get("reason"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def compute_plan_signature(state: AgentState) -> str:
    """action + target_artifact + anchor_hash."""
    payload = state.get("input_payload") or {}
    contract = payload.get("turn_contract") or {}
    action = str(contract.get("primary_op") or "")
    if not action:
        intervention = payload.get("mission_intervention") or {}
        action = str(intervention.get("action") or "")
    if not action:
        intent = payload.get("writing_intent") or {}
        action = str(intent.get("action") or "")
    target = ""
    cmd = payload.get("writing_command") or {}
    if isinstance(cmd, dict):
        target = str(cmd.get("target_filename") or cmd.get("target_kind") or "")
    if not target:
        ms = state.get("manuscript") or {}
        if action in ("edit_plot", "rewrite_outline", "review_outline", "write_outline"):
            target = str(ms.get("outline_path") or "outline")
        elif action in ("write_body", "append_body", "reset_body"):
            target = str(ms.get("body_path") or "body")
    return f"{action}|{target}|{_anchor_hash(payload)}"


def _artifact_byte_delta(observation: dict[str, Any]) -> int:
    delta = observation.get("artifact_delta") or {}
    if isinstance(delta, dict) and delta.get("has_change"):
        outline = int(delta.get("outline_bytes_delta") or 0)
        body = int(delta.get("body_bytes_delta") or 0)
        return abs(outline) + abs(body)
    manuscript = observation.get("manuscript") or {}
    prev = observation.get("prev_manuscript") or {}
    if isinstance(manuscript, dict) and isinstance(prev, dict):
        ob = int(manuscript.get("outline_bytes") or 0) - int(prev.get("outline_bytes") or 0)
        bb = int(manuscript.get("body_bytes") or 0) - int(prev.get("body_bytes") or 0)
        return abs(ob) + abs(bb)
    return 0


def _edit_count(state: AgentState) -> int:
    count = 0
    for item in state.get("tool_results") or []:
        if isinstance(item, dict) and item.get("tool") == "edit_text_artifact":
            if item.get("status") == "ok":
                count += 1
    obs = state.get("observation") or {}
    for line in obs.get("tools_executed") or []:
        if isinstance(line, dict) and line.get("tool") == "edit_text_artifact":
            count += 1
    return count


def _progress(state: AgentState) -> dict[str, Any]:
    from app.runtime.state_field_access import progress_from_state

    return dict(progress_from_state(state) or {})


def record_plan_signature(state: AgentState) -> dict[str, Any]:
    """Append current plan signature to progress; return stall metadata."""
    progress = _progress(state)
    sig = compute_plan_signature(state)
    history: list[str] = list(progress.get("plan_signature_history") or [])
    history.append(sig)
    max_hist = 8
    if len(history) > max_hist:
        history = history[-max_hist:]
    progress["plan_signature_history"] = history
    progress["last_plan_signature"] = sig
    return progress


def detect_stall(state: AgentState) -> dict[str, Any] | None:
    """
    Return stall info when the same plan signature repeats with negligible artifact change.

    Uses stall_budget (default 2) consecutive identical signatures.
    """
    stall_budget = int(getattr(settings, "MISSION_STALL_BUDGET", 2))
    min_byte_change = int(getattr(settings, "MISSION_STALL_MIN_BYTE_DELTA", 200))

    progress = _progress(state)
    history: list[str] = list(progress.get("plan_signature_history") or [])
    if len(history) < stall_budget:
        return None

    recent = history[-stall_budget:]
    if len(set(recent)) != 1:
        return None

    observation = state.get("observation") or {}
    byte_delta = _artifact_byte_delta(observation)
    edits = _edit_count(state)
    if byte_delta >= min_byte_change or edits > 0 and byte_delta > 0:
        return None

    stall_count = int(progress.get("stall_count") or 0) + 1
    return {
        "stalled": True,
        "signature": recent[-1],
        "stall_count": stall_count,
        "byte_delta": byte_delta,
        "edit_count": edits,
    }


def apply_stall_progress(state: AgentState, stall: dict[str, Any]) -> dict[str, Any]:
    from datetime import datetime, timezone

    progress = _progress(state)
    progress["stall_count"] = int(stall.get("stall_count") or 0)
    progress["last_stall_signature"] = stall.get("signature")
    progress["last_stall_at"] = stall.get("at") or datetime.now(timezone.utc).isoformat()
    return progress
