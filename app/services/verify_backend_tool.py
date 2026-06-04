"""Whitelisted project/code verify tool (no arbitrary shell)."""

from __future__ import annotations

from typing import Any

from app.services.project_verify.backends import resolve_project_backend_id, verify_project
from app.services.project_verify.config import allowed_backend_ids


def handle_verify_backend(params: dict[str, Any]) -> dict[str, Any]:
    task_id = str(params.get("task_id") or "").strip()
    if not task_id:
        return {
            "status": "error",
            "ok": False,
            "backend": "",
            "issues": ["missing_task_id"],
        }
    intent_kind = str(params.get("intent_kind") or "code")
    goal = str(params.get("goal") or "")
    requested = str(params.get("backend_id") or "").strip() or None
    if requested and requested not in allowed_backend_ids():
        return {
            "status": "error",
            "ok": False,
            "backend": requested,
            "issues": ["backend_not_whitelisted"],
        }
    bid = requested or resolve_project_backend_id(intent_kind=intent_kind, goal=goal)
    if not bid:
        return {
            "status": "error",
            "ok": False,
            "backend": "",
            "issues": ["no_backend_for_intent"],
        }
    result = verify_project(
        task_id,
        intent_kind=intent_kind,
        goal=goal,
        backend_id=bid,
    )
    out = result.to_dict()
    out["status"] = "ok" if result.ok else "error"
    return out
