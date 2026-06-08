"""Incremental planning — impact analysis and plan graph (WP-1.4)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from app.runtime.state import AgentState, merge_state

PlanNodeStatus = Literal["valid", "stale", "invalid", "must_rerun"]


def _execution_version(state: AgentState) -> int:
    try:
        return int(state.get("execution_version") or 1)
    except (TypeError, ValueError):
        return 1


def _bump_execution_version(state: AgentState) -> int:
    return _execution_version(state) + 1


def build_plan_graph_from_plan(plan: list[str] | None) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    for idx, step in enumerate(plan or []):
        nodes.append(
            {
                "id": f"step-{idx + 1}",
                "description": str(step),
                "status": "valid",
            }
        )
    return {"nodes": nodes, "version": 1}


def analyze_plan_impact(state: AgentState) -> dict[str, Any]:
    """Local invalidation analysis from event_type and payload hints."""
    event_type = str(state.get("event_type") or "new_task")
    payload = state.get("input_payload") or {}
    invalidations: list[dict[str, Any]] = []

    if event_type == "interrupt":
        invalidations.append({"scope": "all_pending", "status": "invalid", "reason": "interrupt"})
    elif event_type == "redirect":
        invalidations.append({"scope": "goal", "status": "must_rerun", "reason": "redirect"})
    elif event_type == "clarification":
        target_files = payload.get("target_files") or payload.get("steer_target_files") or []
        if not (isinstance(target_files, list) and target_files):
            invalidations.append({"scope": "constraints", "status": "stale", "reason": "clarification"})
    elif event_type == "reject":
        invalidations.append({"scope": "output_plan", "status": "invalid", "reason": "reject"})
    elif event_type == "resume":
        invalidations.append({"scope": "checkpoint", "status": "valid", "reason": "resume"})

    target_files = payload.get("target_files") or payload.get("steer_target_files") or []
    if isinstance(target_files, list) and target_files:
        invalidations.append(
            {
                "scope": "files",
                "targets": [str(f) for f in target_files],
                "status": "must_rerun",
                "reason": "file_scope_change",
            }
        )

    return {
        "event_type": event_type,
        "invalidations": invalidations,
        "full_replan": event_type in {"interrupt", "redirect", "new_task"},
        "local_only": event_type == "clarification" and not payload.get("replace_goal"),
    }


def apply_plan_invalidations(
    plan_graph: dict[str, Any],
    invalidations: list[dict[str, Any]],
) -> dict[str, Any]:
    graph = dict(plan_graph or {"nodes": []})
    nodes = [dict(n) for n in graph.get("nodes") or [] if isinstance(n, dict)]
    if not nodes:
        return graph

    for inv in invalidations:
        status = str(inv.get("status") or "stale")
        scope = str(inv.get("scope") or "")
        if scope in {"all_pending", "goal", "output_plan"}:
            for node in nodes:
                if node.get("status") != "valid" or status in {"invalid", "must_rerun"}:
                    node["status"] = status
        elif scope == "files":
            targets = {str(t).lower() for t in inv.get("targets") or []}
            for node in nodes:
                desc = str(node.get("description") or "").lower()
                if any(t in desc for t in targets):
                    node["status"] = status
        elif scope == "constraints":
            for node in nodes:
                if node.get("status") == "valid":
                    node["status"] = "stale"

    graph["nodes"] = nodes
    return graph


def prepare_incremental_planning_state(state: AgentState) -> AgentState:
    """Stamp plan invalidations and execution_version before delegating to planning core."""
    impact = analyze_plan_impact(state)
    existing_graph = state.get("plan_graph") if isinstance(state.get("plan_graph"), dict) else {}
    if not existing_graph.get("nodes") and state.get("plan"):
        existing_graph = build_plan_graph_from_plan(state.get("plan"))

    updated_graph = apply_plan_invalidations(existing_graph, impact["invalidations"])
    version = _bump_execution_version(state) if impact["full_replan"] else _execution_version(state)

    payload = dict(state.get("input_payload") or {})
    if impact["local_only"]:
        payload.pop("require_planning_after_steer", None)
        payload["incremental_plan_update"] = True

    return merge_state(
        state,
        input_payload=payload,
        plan_graph=updated_graph,
        plan_invalidations=impact,
        execution_version=version,
    )
