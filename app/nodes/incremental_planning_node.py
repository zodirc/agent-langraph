"""Incremental planning node — delegates to planning core after impact analysis (WP-1.4)."""

from __future__ import annotations

from app.nodes.planning_node import planning_node
from app.runtime.state import AgentState
from app.services.incremental_planning import prepare_incremental_planning_state
from app.services.state_store import get_state_store


def incremental_planning_node(state: AgentState) -> AgentState:
    from app.runtime.state import TaskStatus, merge_state
    from app.services.reasoning_trace import report_boundary
    from app.services.stream_progress import report_progress

    prepared = prepare_incremental_planning_state(state)
    prepared = merge_state(
        prepared,
        status=TaskStatus.RUNNING.value,
        current_node="incremental_planning",
    )
    get_state_store().save(prepared)
    report_progress("正在规划本回合步骤…")
    report_boundary("incremental_planning", "enter")
    from app.services.mode_freeze import record_planning_mode_entry

    record_planning_mode_entry(prepared, phase="incremental_planning_enter")
    updated = planning_node(prepared)
    updated = dict(updated)
    updated["current_node"] = "incremental_planning"
    if isinstance(updated.get("plan"), list):
        from app.services.incremental_planning import build_plan_graph_from_plan

        graph = build_plan_graph_from_plan(updated.get("plan"))
        inv = updated.get("plan_invalidations") or prepared.get("plan_invalidations")
        if isinstance(inv, dict):
            from app.services.incremental_planning import apply_plan_invalidations

            graph = apply_plan_invalidations(graph, inv.get("invalidations") or [])
        updated["plan_graph"] = graph
    get_state_store().save(updated)  # type: ignore[arg-type]
    return updated  # type: ignore[return-value]
