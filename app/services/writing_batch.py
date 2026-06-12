"""Multi-chapter batch continuation within a single user turn."""

from __future__ import annotations

from typing import Any

from app.runtime.state import AgentState, append_audit, merge_state
from app.services.writing_project import (
    batch_has_remaining,
    load_project,
    parse_target_chapter_from_goal,
    save_project,
)

_BATCH_MAX_CHAPTERS_PER_TURN = 4
_BODY_WRITE_TOOLS = frozenset({"write_text_artifact", "append_text_artifact"})


def _turn_had_successful_body_write(state: AgentState) -> bool:
    from app.services.writing_project import load_project

    task_id = str(state["task_id"])
    project = load_project(task_id)
    if not project:
        return False
    expected = project.body_file.replace("\\", "/")
    for item in state.get("tool_results") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("tool") or "") not in _BODY_WRITE_TOOLS:
            continue
        if str(item.get("status") or "ok") not in ("ok", "cached"):
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        fname = str(result.get("filename") or "").replace("\\", "/")
        if fname == expected:
            return True
    return False


def batch_chapters_written_this_turn(state: AgentState) -> int:
    payload = state.get("input_payload") or {}
    return int(payload.get("writing_batch_chapters_written") or 0)


def maybe_schedule_batch_continuation(state: AgentState) -> AgentState | None:
    """After a successful chapter write, queue the next chapter if batch target remains."""
    payload = state.get("input_payload") or {}
    intent = payload.get("writing_intent") or {}
    if not intent.get("enabled"):
        return None
    if not _turn_had_successful_body_write(state):
        return None

    task_id = str(state["task_id"])
    goal = str(payload.get("goal") or payload.get("query") or "").strip()
    project = load_project(task_id)
    if not project:
        return None
    if goal:
        target = parse_target_chapter_from_goal(goal)
        if target and target > project.target_chapter:
            project.target_chapter = target
            save_project(task_id, project)

    if not batch_has_remaining(project):
        return None
    if batch_chapters_written_this_turn(state) >= _BATCH_MAX_CHAPTERS_PER_TURN:
        return None

    from app.services.writing_playbook import apply_writing_playbook

    actions, plan, patched = apply_writing_playbook(
        [],
        operator="append",
        goal=goal or "续写下一章",
        task_id=task_id,
        state=state,
        force_write=True,
    )
    if not patched or not actions:
        return None

    from app.nodes.planning_node import _execution_transport_from_actions

    exec_tools, tool_params, stages = _execution_transport_from_actions(actions)
    new_payload = dict(payload)
    new_payload["writing_operator"] = "append"
    new_payload["writing_intent"] = {**intent, "enabled": True, "source": "writing_batch"}
    new_payload["force_write_after_reads"] = True
    new_payload["writing_batch_chapters_written"] = batch_chapters_written_this_turn(state) + 1
    new_payload["tool_params"] = {**new_payload.get("tool_params", {}), **tool_params}
    new_payload["tool_stages"] = stages
    new_payload["thin_execution_profile"] = "writing_batch"
    new_payload["skip_retrieval"] = True
    if project.current_chapter_incomplete:
        batch_hint = (
            f"Batch writing: continue chapter {project.next_chapter} "
            f"(incomplete — append only to the chapter tail)."
        )
    else:
        batch_hint = (
            f"Batch writing: write chapter {project.next_chapter}"
            f"{f' of {project.target_chapter}' if project.target_chapter else ''}. "
            "Append only the new chapter prose to the novel body file."
        )
    new_payload["route_audit_replan_feedback"] = batch_hint
    new_payload.pop("route_audit_replan", None)

    return merge_state(
        state,
        input_payload=new_payload,
        plan=plan,
        planned_actions=[a.to_dict() for a in actions],
        selected_tools=exec_tools,
        skip_retrieval=True,
        status="PLANNED",
        current_node="planning",
        audit_log=append_audit(
            state,
            "writing_batch",
            "schedule_next_chapter",
            {
                "next_chapter": project.next_chapter,
                "target_chapter": project.target_chapter,
                "batch_written": new_payload["writing_batch_chapters_written"],
            },
        ),
    )


def reset_batch_turn_counter(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    out.pop("writing_batch_chapters_written", None)
    return out
