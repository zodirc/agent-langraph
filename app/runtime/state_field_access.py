"""Accessors for runtime data folded into §2.2 field families (WP-4.2)."""

from __future__ import annotations

from typing import Any

from app.runtime.state import AgentState, merge_state


def mission_from_state(state: AgentState | dict[str, Any]) -> dict[str, Any] | None:
    payload = state.get("input_payload") or {}
    mission = payload.get("mission")
    if isinstance(mission, dict):
        return mission
    plan_graph = state.get("plan_graph") or {}
    meta = plan_graph.get("meta") if isinstance(plan_graph, dict) else {}
    if isinstance(meta, dict) and isinstance(meta.get("mission"), dict):
        return meta["mission"]
    return None


def mission_control_from_state(state: AgentState | dict[str, Any]) -> dict[str, Any]:
    """Mission control snapshot (pause reason, done flag) from §2.2 field families."""
    raw = state.get("mission_control")
    if isinstance(raw, dict):
        return raw
    plan_graph = state.get("plan_graph") or {}
    meta = plan_graph.get("meta") if isinstance(plan_graph, dict) else {}
    if isinstance(meta, dict) and isinstance(meta.get("mission_control"), dict):
        return meta["mission_control"]
    return {}


def progress_from_state(state: AgentState | dict[str, Any]) -> dict[str, Any] | None:
    bg = state.get("background_status") or {}
    if isinstance(bg.get("progress"), dict):
        return bg["progress"]
    plan_graph = state.get("plan_graph") or {}
    meta = plan_graph.get("meta") if isinstance(plan_graph, dict) else {}
    if isinstance(meta, dict) and isinstance(meta.get("progress"), dict):
        return meta["progress"]
    return None


def set_progress_on_state(state: AgentState, progress: dict[str, Any]) -> AgentState:
    bg = dict(state.get("background_status") or {})
    bg["progress"] = progress
    plan_graph = dict(state.get("plan_graph") or {"nodes": []})
    meta = dict(plan_graph.get("meta") or {})
    meta["progress"] = progress
    plan_graph["meta"] = meta
    return merge_state(state, background_status=bg, plan_graph=plan_graph)


def react_loop_from_state(state: AgentState | dict[str, Any]) -> dict[str, Any] | None:
    plan_graph = state.get("plan_graph") or {}
    meta = plan_graph.get("meta") if isinstance(plan_graph, dict) else {}
    loop = meta.get("react_loop") if isinstance(meta, dict) else None
    return loop if isinstance(loop, dict) else None


def set_react_loop_on_state(state: AgentState, react_loop: dict[str, Any] | None) -> AgentState:
    plan_graph = dict(state.get("plan_graph") or {"nodes": []})
    meta = dict(plan_graph.get("meta") or {})
    if react_loop is None:
        meta.pop("react_loop", None)
    else:
        meta["react_loop"] = react_loop
    plan_graph["meta"] = meta
    return merge_state(state, plan_graph=plan_graph)


def _plan_graph_meta(state: AgentState | dict[str, Any]) -> dict[str, Any]:
    plan_graph = state.get("plan_graph") or {}
    meta = plan_graph.get("meta") if isinstance(plan_graph, dict) else {}
    return meta if isinstance(meta, dict) else {}


def subtasks_from_state(state: AgentState | dict[str, Any]) -> list[dict[str, Any]]:
    raw = state.get("subtasks")
    if isinstance(raw, list):
        return list(raw)
    meta = _plan_graph_meta(state)
    subtasks = meta.get("subtasks")
    return list(subtasks) if isinstance(subtasks, list) else []


def worker_results_from_state(state: AgentState | dict[str, Any]) -> dict[str, Any]:
    raw = state.get("worker_results")
    if isinstance(raw, dict):
        return dict(raw)
    meta = _plan_graph_meta(state)
    results = meta.get("worker_results")
    return dict(results) if isinstance(results, dict) else {}
