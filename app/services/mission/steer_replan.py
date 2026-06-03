"""
Steer replan — canonical work_plan patch after planning interprets steer.

Single source of truth for queue order after intervention; emits impact manifest.
"""

from __future__ import annotations

from typing import Any, Optional

from app.runtime.state import AgentState, append_audit, merge_state
from app.services.confirmation.config import load_confirmation_gates_config
from app.services.mission_intervention import intervention_from_payload, is_forced


def intervention_to_work_item(
    intervention: dict[str, Any],
    *,
    step: int,
) -> Optional[dict[str, Any]]:
    action = str(intervention.get("action") or "")
    if not action:
        return None
    wi = intervention.get("work_item")
    if isinstance(wi, dict) and wi.get("kind"):
        return {
            "id": str(wi.get("id") or f"wi-steer-{step}"),
            "kind": str(wi.get("kind")),
            "title": str(wi.get("title") or wi.get("kind")),
            "status": "pending",
            "params": dict(wi.get("params") or {}),
        }

    mapping = {
        "rewrite_outline": ("write_outline", "rewrite outline"),
        "reset_body": ("reset_body", "reset body"),
        "edit_plot": ("edit_plot", "edit plot"),
        "review_outline": ("human_gate", "review outline"),
    }
    if action not in mapping:
        return None
    kind, title = mapping[action]
    params: dict[str, Any] = {}
    if action == "edit_plot":
        params["edit_spec"] = dict(intervention.get("edit_spec") or {})
    return {
        "id": f"wi-steer-{step}-{action}",
        "kind": kind,
        "title": title,
        "status": "pending",
        "params": params,
    }


def _plan(state: AgentState) -> dict[str, Any]:
    return dict((state.get("progress") or {}).get("work_plan") or {})


def build_impact_manifest(
    state: AgentState,
    *,
    cancelled_ids: list[str],
    prepended: list[dict[str, Any]],
    intervention: dict[str, Any],
) -> dict[str, Any]:
    operations: list[dict[str, Any]] = []
    action = str(intervention.get("action") or "")

    for wid in cancelled_ids:
        operations.append({"op": "cancel_pending", "work_item_id": wid})

    if action == "reset_body":
        manuscript = state.get("manuscript") or {}
        operations.append(
            {
                "op": "overwrite",
                "artifact": manuscript.get("body_path") or "novel.txt",
                "note": "body will be archived then rewritten",
            }
        )
    elif action == "rewrite_outline":
        manuscript = state.get("manuscript") or {}
        operations.append(
            {
                "op": "overwrite",
                "artifact": manuscript.get("outline_path") or "outline.txt",
            }
        )

    for item in prepended:
        operations.append(
            {
                "op": "prepend",
                "work_item_id": item.get("id"),
                "kind": item.get("kind"),
                "title": item.get("title"),
            }
        )

    next_queue = [
        {"id": i.get("id"), "kind": i.get("kind"), "title": i.get("title"), "status": "next"}
        for i in prepended[:3]
    ]

    return {
        "operations": operations,
        "next_queue": next_queue,
    }


def apply_work_plan_patch(
    state: AgentState,
    patch: Optional[dict[str, Any]],
    *,
    intervention: Optional[dict[str, Any]] = None,
) -> AgentState:
    """
    Apply planning work_plan_patch: cancel conflicting pending items, prepend new items.
    Falls back to intervention_to_work_item when patch empty but forced intervention present.
    """
    cfg = load_confirmation_gates_config()
    payload = dict(state.get("input_payload") or {})
    intervention = intervention or intervention_from_payload(payload) or {}
    step = int(state.get("mission_step") or 1)

    patch = patch if isinstance(patch, dict) else {}
    cancel_ids = [str(x) for x in (patch.get("cancel_ids") or [])]
    prepend_raw = patch.get("prepend") or patch.get("prepend_items") or []
    prepend: list[dict[str, Any]] = []
    for row in prepend_raw:
        if isinstance(row, dict) and row.get("kind"):
            prepend.append(
                {
                    "id": str(row.get("id") or f"wi-patch-{len(prepend)}"),
                    "kind": str(row["kind"]),
                    "title": str(row.get("title") or row["kind"]),
                    "status": "pending",
                    "params": dict(row.get("params") or {}),
                }
            )

    if not prepend and intervention and is_forced(intervention):
        wi = intervention_to_work_item(intervention, step=step)
        if wi:
            prepend = [wi]

    action = str(intervention.get("action") or "")
    auto_cancel = cfg.cancel_on_intervention.get(action, frozenset())
    cancel_kinds = {str(k) for k in (patch.get("cancel_kinds") or [])}
    plan = _plan(state)
    items = list(plan.get("items") or [])

    cancelled: list[str] = []
    for row in items:
        wid = str(row.get("id") or "")
        if wid in cancel_ids:
            row["status"] = "cancelled"
            cancelled.append(wid)
            continue
        row_kind = str(row.get("kind") or "")
        if (
            str(row.get("status") or "pending") == "pending"
            and (row_kind in auto_cancel or row_kind in cancel_kinds)
        ):
            row["status"] = "cancelled"
            cancelled.append(wid)

    insert_at = 0
    for i, row in enumerate(items):
        if str(row.get("status") or "") not in ("cancelled", "done"):
            insert_at = i
            break
    else:
        insert_at = len(items)

    for offset, item in enumerate(prepend):
        items.insert(insert_at + offset, {**item, "status": "pending"})

    plan["items"] = items
    plan["total_items"] = len(items)
    plan["current_id"] = None

    impact = build_impact_manifest(
        state,
        cancelled_ids=cancelled,
        prepended=prepend,
        intervention=intervention,
    )

    progress = dict(state.get("progress") or {})
    progress["work_plan"] = plan

    payload["steer_replan_impact"] = impact

    return merge_state(
        state,
        progress=progress,
        input_payload=payload,
        audit_log=append_audit(
            state,
            "steer_replan",
            "applied",
            {
                "cancelled": cancelled,
                "prepended": [i.get("id") for i in prepend],
                "intervention_action": action,
            },
        ),
    )
