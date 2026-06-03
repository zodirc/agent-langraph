"""Supervisor 分解

supervisor_decompose — goal → subtasks[].
domain.worker_executor.decompose_task(goal, domains)
Writes: subtasks, plan, execution_mode
Graph: decompose → supervisor_worker → supervisor_merge → policy → …"""

from __future__ import annotations

import json

from app.domain.worker_executor import decompose_task
from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.state_store import get_state_store


def supervisor_decompose_node(state: AgentState) -> AgentState:
    """
    Supervisor: decompose goal into domain-specific subtasks.

    Writes: subtasks, plan, execution_mode, status, audit_log
    """
    try:
        from app.services.manuscript_supervisor_guard import reject_supervisor_for_manuscript

        blocked = reject_supervisor_for_manuscript(state)
        if blocked is not None:
            get_state_store().save(blocked)
            return blocked

        payload = state.get("input_payload", {})
        goal = str(payload.get("goal") or payload.get("query") or "")
        domains = payload.get("domains")
        if isinstance(domains, str):
            domains = [d.strip() for d in domains.split(",") if d.strip()]
        if not isinstance(domains, list):
            domains = None

        subtasks = decompose_task(goal, domains=domains)
        plan = [
            f"worker:{item['domain']}:{item.get('required_capability', item['domain'])}:"
            f"{item['description'][:48]}"
            for item in subtasks
        ]

        updated = merge_state(
            state,
            execution_mode="supervisor",
            subtasks=subtasks,
            worker_results={},
            plan=plan,
            status=TaskStatus.PLANNED.value,
            current_node="supervisor_decompose",
            audit_log=append_audit(
                state,
                "supervisor_decompose",
                "success",
                {"subtask_count": len(subtasks)},
            ),
        )
        get_state_store().save(updated)
        return updated
    except Exception as exc:
        return merge_state(
            state,
            errors=list(state.get("errors", [])) + [f"supervisor_decompose: {exc}"],
            status=TaskStatus.FAILED.value,
            current_node="supervisor_decompose",
            audit_log=append_audit(state, "supervisor_decompose", "error", {"detail": str(exc)}),
        )
