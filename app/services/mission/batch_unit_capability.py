"""
Batch unit quality capability — registry metadata for planning (no goal keywords).

Injected via runtime_capabilities when manuscript has written chapters and steer is active.
"""

from __future__ import annotations

from typing import Any, Optional

from app.runtime.state import AgentState


def batch_unit_capability_descriptor() -> dict[str, Any]:
    return {
        "id": "batch_unit_quality",
        "when": (
            "User steers to review or revise already-written chapters (quality pass over "
            "existing body units). Requires last_chapter_index >= 1."
        ),
        "planning": (
            'Set mission_intervention {"action":"batch_unit_quality","force":true} and '
            'turn_contract {"primary_op":"batch_unit_quality","forbid":["append_body","write_body"],'
            '"override_step_policy":true}. '
            'Emit work_plan_patch: cancel pending append_* items; prepend review_chapter per '
            'chapter_index (optionally polish_chapter with depends_on after each review). '
            'Do NOT use execution_grant or append_body until batch work completes.'
        ),
        "work_item_kinds": ["review_chapter", "polish_chapter"],
        "graph": "mission loop: review → polish (if gate failed) per chapter",
    }


def batch_unit_context_for_planning(state: AgentState) -> Optional[dict[str, Any]]:
    """Structural facts for planning user JSON (no NLP)."""
    from app.services.mission.batch_unit_work_plan import _last_written_chapter

    last = _last_written_chapter(state)
    if last < 1:
        return None
    payload = state.get("input_payload") or {}
    if not (
        payload.get("steer_applied_at")
        or payload.get("require_planning_after_steer")
        or payload.get("steer_planning_done") is False
    ):
        return None
    progress = state.get("progress") if isinstance(state.get("progress"), dict) else {}
    plan = progress.get("work_plan") if isinstance(progress.get("work_plan"), dict) else {}
    pending_forward = sum(
        1
        for row in plan.get("items") or []
        if isinstance(row, dict)
        and str(row.get("status") or "pending") == "pending"
        and str(row.get("kind") or "") in ("append_body", "append_chapter", "write_body")
    )
    return {
        "last_written_chapter": last,
        "pending_forward_write_items": pending_forward,
        "capability": batch_unit_capability_descriptor(),
    }


def enrich_planning_result_with_batch_unit(
    result: dict[str, Any],
    state: AgentState,
) -> dict[str, Any]:
    """
    When LLM omitted batch contract but structural steer+chapters match, merge fallback patch.
    """
    from app.services.mission.batch_unit_work_plan import build_batch_unit_planning_fallback

    primary = ""
    raw_contract = result.get("turn_contract")
    if isinstance(raw_contract, dict):
        primary = str(raw_contract.get("primary_op") or "")
    if primary == "batch_unit_quality":
        return result
    patch = result.get("work_plan_patch")
    if isinstance(patch, dict):
        prepend = patch.get("prepend") or patch.get("prepend_items") or []
        if any(
            isinstance(row, dict) and str(row.get("kind")) == "review_chapter"
            for row in prepend
        ):
            return result

    fb = build_batch_unit_planning_fallback(state)
    if not fb:
        return result

    patch = result.get("work_plan_patch")
    has_review_patch = isinstance(patch, dict) and any(
        isinstance(row, dict) and str(row.get("kind")) == "review_chapter"
        for row in (patch.get("prepend") or patch.get("prepend_items") or [])
    )
    wrong_primary = primary in ("pause", "reasoning", "explain_only", "")
    if wrong_primary or (has_review_patch and primary != "batch_unit_quality"):
        out = dict(result)
        out["turn_contract"] = fb["turn_contract"]
        out["mission_intervention"] = fb.get("mission_intervention") or out.get(
            "mission_intervention"
        )
        out["writing_intent"] = fb["writing_intent"]
        if not has_review_patch:
            out["work_plan_patch"] = fb["work_plan_patch"]
        if not out.get("selected_tools"):
            out["selected_tools"] = fb.get("selected_tools")
        out.setdefault("parser_fallback", True)
        out["fallback_reason"] = "batch_unit_override_wrong_primary"
        return out

    out = dict(result)
    for key, val in fb.items():
        if key not in out or out.get(key) in (None, [], {}):
            out[key] = val
    return out
