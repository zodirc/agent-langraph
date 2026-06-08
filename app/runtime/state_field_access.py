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
