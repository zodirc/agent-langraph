"""Engineering mode bounded execution node."""

from __future__ import annotations

from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.engineering_execution import format_engineering_answer, run_engineering_bounded
from app.services.project_verify.backends import ProjectVerifyResult
from app.services.reasoning_trace import report_boundary
from app.services.state_store import get_state_store
from app.services.stream_progress import report_progress


def engineering_execution_node(state: AgentState) -> AgentState:
    report_progress("工程模式：落盘、校验与收尾…")
    report_boundary("engineering_execution", "enter")
    try:
        updated = run_engineering_bounded(state)
        get_state_store().save(updated)
        return updated
    except Exception as exc:
        from app.services.execution_control import handle_control_exception

        handled = handle_control_exception(state, exc)
        if handled is not None:
            get_state_store().save(handled)
            return handled

        answer = format_engineering_answer(
            summary="工程执行节点异常",
            written_files=[],
            preview="",
            verify_result=ProjectVerifyResult(
                ok=False,
                backend="",
                status="degraded",
                issues=["engineering_node_exception"],
                stderr=str(exc),
            ),
            degraded_reason=f"engineering_execution 未捕获异常：{exc}",
        )
        updated = merge_state(
            state,
            input_payload={
                **(state.get("input_payload") or {}),
                "engineering_trace": {"error": str(exc)},
            },
            final_answer=answer,
            errors=list(state.get("errors", [])) + [f"engineering_execution: {exc}"],
            status=TaskStatus.FAILED.value,
            current_node="engineering_execution",
            audit_log=append_audit(
                state,
                "engineering_execution",
                "error",
                {"detail": str(exc)},
            ),
        )
        get_state_store().save(updated)
        return updated
