"""Engineering mode bounded execution node."""

from __future__ import annotations

from app.runtime.state import AgentState, merge_state
from app.services.engineering_execution import run_engineering_bounded
from app.services.reasoning_trace import report_boundary
from app.services.stream_progress import report_progress


def engineering_execution_node(state: AgentState) -> AgentState:
    report_progress("工程模式：落盘、校验与收尾…")
    report_boundary("engineering_execution", "enter")
    try:
        return run_engineering_bounded(state)
    except Exception as exc:
        return merge_state(
            state,
            errors=list(state.get("errors", [])) + [f"engineering_execution: {exc}"],
            status="FAILED",
            current_node="engineering_execution",
        )
