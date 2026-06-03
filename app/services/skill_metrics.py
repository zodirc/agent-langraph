"""Skill-dimension metrics and task outcome recording."""

from __future__ import annotations

from typing import Any, Optional

from app.runtime.state import AgentState, TaskStatus


def _skill_labels(state: AgentState | dict[str, Any]) -> tuple[str, str, str]:
    skill_id = str(state.get("skill_id") or "none")[:64]
    version = str(state.get("skill_version") or "unknown")[:32]
    source = str(state.get("skill_source_type") or "unknown")[:24]
    return skill_id, version, source


def record_skill_task_started(state: AgentState | dict[str, Any]) -> None:
    if not state.get("skill_id"):
        return
    skill_id, version, source = _skill_labels(state)
    from app.services.metrics_service import get_metrics_service

    get_metrics_service().inc_skill_invocation(skill_id, version=version, source_type=source)


def record_skill_task_finished(state: AgentState | dict[str, Any]) -> None:
    skill_id = state.get("skill_id")
    if not skill_id:
        return
    sid, version, source = _skill_labels(state)
    status = str(state.get("status") or "unknown")
    review = bool(state.get("review_required"))
    from app.services.metrics_service import get_metrics_service

    svc = get_metrics_service()
    if status == TaskStatus.COMPLETED.value:
        svc.inc_skill_outcome(sid, version=version, source_type=source, outcome="completed")
    elif status in (TaskStatus.FAILED.value, TaskStatus.DEAD_LETTER.value, TaskStatus.REJECTED.value):
        svc.inc_skill_outcome(sid, version=version, source_type=source, outcome="failed")
    else:
        svc.inc_skill_outcome(sid, version=version, source_type=source, outcome=status.lower()[:20])
    if review:
        svc.inc_skill_review(sid, version=version)
    _record_skill_tools(state, sid, version)


def _record_skill_tools(state: AgentState | dict[str, Any], skill_id: str, version: str) -> None:
    tools: set[str] = set()
    for item in state.get("tool_results") or []:
        if isinstance(item, dict):
            name = item.get("tool") or item.get("name")
            if name:
                tools.add(str(name))
    for name in state.get("selected_tools") or []:
        if name:
            tools.add(str(name))
    if not tools:
        return
    from app.services.metrics_service import get_metrics_service

    svc = get_metrics_service()
    for tool in sorted(tools):
        svc.inc_skill_tool_usage(skill_id, tool_name=tool, version=version)


def record_skill_validation_failed(state: AgentState | dict[str, Any], issue_count: int) -> None:
    if not state.get("skill_id") or issue_count <= 0:
        return
    sid, version, _source = _skill_labels(state)
    from app.services.metrics_service import get_metrics_service

    get_metrics_service().inc_skill_validation_failed(sid, version=version, count=issue_count)
