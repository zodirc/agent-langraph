"""
Batch unit work-plan projection — build review/polish agenda from manuscript facts.

Used when planning steers into multi-chapter quality work (no goal keyword matching).
"""

from __future__ import annotations

from typing import Any, Optional

from app.runtime.state import AgentState

_FORWARD_WRITE_KINDS = frozenset(
    {"append_body", "append_chapter", "write_body", "write_chapter"}
)


def _last_written_chapter(state: AgentState) -> int:
    manuscript = state.get("manuscript") if isinstance(state.get("manuscript"), dict) else {}
    last = int(manuscript.get("last_chapter_index") or 0)
    if last > 0:
        return last
    progress = state.get("progress") if isinstance(state.get("progress"), dict) else {}
    metrics = progress.get("metrics") if isinstance(progress.get("metrics"), dict) else {}
    return max(0, int(metrics.get("last_chapter_index") or 0))


def should_project_batch_unit_work_plan(state: AgentState) -> bool:
    """
    Structural steer replan: body has chapters and work_plan still has forward-write pending.
    """
    from app.services.intent_composer import grant_may_mechanical_forward

    payload = state.get("input_payload") or {}
    if grant_may_mechanical_forward(payload, state=state):
        return False
    if not (payload.get("steer_applied_at") or payload.get("require_planning_after_steer")):
        return False
    if _last_written_chapter(state) < 1:
        return False
    progress = state.get("progress") if isinstance(state.get("progress"), dict) else {}
    plan = progress.get("work_plan") if isinstance(progress.get("work_plan"), dict) else {}
    for row in plan.get("items") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "pending") != "pending":
            continue
        if str(row.get("kind") or "") in _FORWARD_WRITE_KINDS:
            return True
    return _last_written_chapter(state) >= 1


def build_batch_unit_work_plan_patch(
    state: AgentState,
    *,
    max_units: Optional[int] = None,
) -> dict[str, Any]:
    """Prepend review_chapter items for chapters 1..last_written; cancel forward-write pending."""
    last = _last_written_chapter(state)
    if last < 1:
        return {"cancel_kinds": [], "prepend": []}

    from app.config.settings import settings

    cap = int(max_units or getattr(settings, "MISSION_BATCH_UNIT_MAX_PER_PLAN", 12))
    end = min(last, cap)
    prepend: list[dict[str, Any]] = []
    for chapter in range(1, end + 1):
        review_id = f"wi-batch-review-{chapter}"
        prepend.append(
            {
                "id": review_id,
                "kind": "review_chapter",
                "title": f"review chapter {chapter}",
                "params": {"writing_phase": "review_chapter", "chapter_index": chapter},
            }
        )
        prepend.append(
            {
                "id": f"wi-batch-polish-{chapter}",
                "kind": "polish_chapter",
                "title": f"polish chapter {chapter} if review failed",
                "depends_on": [review_id],
                "params": {"writing_phase": "polish_chapter", "chapter_index": chapter},
            }
        )

    progress = state.get("progress") if isinstance(state.get("progress"), dict) else {}
    plan = progress.get("work_plan") if isinstance(progress.get("work_plan"), dict) else {}
    cancel_ids: list[str] = []
    for row in plan.get("items") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "pending") != "pending":
            continue
        if str(row.get("kind") or "") in _FORWARD_WRITE_KINDS:
            wid = str(row.get("id") or "")
            if wid:
                cancel_ids.append(wid)

    return {
        "cancel_ids": cancel_ids,
        "cancel_kinds": sorted(_FORWARD_WRITE_KINDS),
        "prepend": prepend,
        "batch_meta": {"last_chapter": last, "planned_units": end},
    }


def build_batch_unit_planning_fallback(state: AgentState) -> Optional[dict[str, Any]]:
    """Deterministic planning payload for batch chapter quality work."""
    if not should_project_batch_unit_work_plan(state):
        return None

    patch = build_batch_unit_work_plan_patch(state)
    last = int((patch.get("batch_meta") or {}).get("last_chapter") or 0)
    return {
        "plan": [
            f"batch quality review chapters 1..{last}",
            "revise chapters below quality threshold",
        ],
        "selected_tools": ["read_text_artifact"],
        "writing_intent": {"enabled": False, "action": "batch_unit_quality"},
        "work_plan_patch": patch,
        "turn_contract": {
            "intent_kind": "batch_quality",
            "primary_op": "batch_unit_quality",
            "ops": [{"op": "evaluate", "unit": "chapter"}],
            "tools": ["read_text_artifact"],
            "forbid": ["append_body", "write_body"],
            "override_step_policy": True,
            "user_visible_reason": "steer_batch_unit_review",
        },
        "mission_intervention": {
            "action": "batch_unit_quality",
            "force": True,
            "reason": str((state.get("input_payload") or {}).get("goal") or "")[:240],
        },
        "skip_retrieval": True,
        "risk_level": "LOW",
        "parser_fallback": True,
        "fallback_reason": "batch_unit_work_plan",
    }
