"""
Mission orchestration tools — LLM / client callable (no regex routing).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.services.mission_orchestrator import (
    append_work_items,
    orchestration_enabled,
    work_plan_from_mission,
)
from app.services.state_store import get_state_store


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def handle_enqueue_mission_work_item(params: dict[str, Any]) -> dict[str, Any]:
    """
    Append one or more work items to the mission work plan.

    The planning LLM uses this instead of hard-coded chapter decomposition.
    """
    task_id = str(params["task_id"])
    items = params.get("items")
    if not isinstance(items, list) or not items:
        single = params.get("work_item")
        items = [single] if isinstance(single, dict) else []

    if not items:
        raise ValueError("items or work_item is required")

    stored = get_state_store().load(task_id)
    if not stored:
        raise FileNotFoundError(f"Task not found: {task_id}")
    mission = stored.get("mission") or {}
    if not orchestration_enabled(mission):
        raise ValueError("orchestration is not enabled on this mission")

    normalized: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "custom")
        normalized.append(
            {
                "id": str(raw.get("id") or f"wi-{uuid.uuid4().hex[:8]}"),
                "kind": kind,
                "title": str(raw.get("title") or kind),
                "status": "pending",
                "params": dict(raw.get("params") or {}),
            }
        )

    progress = dict(stored.get("progress") or {})
    plan = dict(progress.get("work_plan") or work_plan_from_mission(mission))
    plan = append_work_items(plan, normalized)
    progress["work_plan"] = plan

    from app.runtime.state import merge_state

    updated = merge_state(stored, progress=progress)
    get_state_store().save(updated)

    return {
        "status": "ok",
        "enqueued": len(normalized),
        "total_items": plan.get("total_items"),
        "item_ids": [i["id"] for i in normalized],
    }


def handle_set_mission_work_plan(params: dict[str, Any]) -> dict[str, Any]:
    """Replace the full work plan (typically from planning LLM decomposition)."""
    task_id = str(params["task_id"])
    plan = params.get("work_plan")
    if not isinstance(plan, dict) or not isinstance(plan.get("items"), list):
        raise ValueError("work_plan.items is required")

    stored = get_state_store().load(task_id)
    if not stored:
        raise FileNotFoundError(f"Task not found: {task_id}")

    items = []
    for raw in plan["items"]:
        if isinstance(raw, dict):
            items.append(
                {
                    "id": str(raw.get("id") or f"wi-{uuid.uuid4().hex[:8]}"),
                    "kind": str(raw.get("kind") or "custom"),
                    "title": str(raw.get("title") or ""),
                    "status": str(raw.get("status") or "pending"),
                    "params": dict(raw.get("params") or {}),
                }
            )

    new_plan = {
        "version": int(plan.get("version") or 1),
        "mode": str(plan.get("mode") or "explicit"),
        "items": items,
        "current_id": plan.get("current_id"),
        "completed_ids": list(plan.get("completed_ids") or []),
        "total_items": len(items),
        "set_at": _now_iso(),
    }

    progress = dict(stored.get("progress") or {})
    progress["work_plan"] = new_plan

    from app.runtime.state import merge_state

    updated = merge_state(stored, progress=progress)
    get_state_store().save(updated)

    return {
        "status": "ok",
        "total_items": len(items),
        "mode": new_plan["mode"],
    }
