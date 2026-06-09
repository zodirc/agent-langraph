"""Shared revision fast-path execution for writing executor and tool_node."""

from __future__ import annotations

from typing import Any

from app.domain.revision_intent import RevisionEdit, RevisionIntent
from app.domain.writing_command import WritingCommand
from app.runtime.state import AgentState, TaskStatus, merge_state
from app.services.writing.revision_command import (
    enrich_revision_edits_from_sections,
    revision_intent_executable,
    revision_intent_to_edit_params,
    revision_intent_to_read_params,
)


def is_revision_tool_path(state: AgentState) -> bool:
    """True when tool_execution should run the revision fast path instead of staged tools."""
    payload = state.get("input_payload") or {}
    if payload.get("thin_execution_profile") == "revision_scoped":
        return True
    intent_obs = state.get("intent_observation") or {}
    if not intent_obs.get("is_revision") and not payload.get("revision_intent"):
        return False
    from app.services.manuscript_service import WRITING_TOOL_NAMES

    tools = [
        t
        for t in (state.get("selected_tools") or [])
        if t not in WRITING_TOOL_NAMES
    ]
    if "read_text_artifact" not in tools or "edit_text_artifact" not in tools:
        return False
    revision_intent = payload.get("revision_intent") or intent_obs.get("revision_intent")
    return revision_intent_executable(revision_intent)


def _resolve_edits_from_content(
    rev: RevisionIntent,
    content: str,
    goal: str,
) -> RevisionIntent:
    """Fill missing anchors via scoped read + lightweight edit planning."""
    if any(e.old_text or e.start_line is not None for e in rev.edits):
        return rev

    from app.services.edit_scope import analyze_edit_scope, should_rewrite_outline_not_patch
    from app.services.outline_steer_patch import plan_edit_from_read_content

    global_rewrite = rev.revision_scope == "full"
    if not global_rewrite:
        scope = analyze_edit_scope(goal, outline_excerpt=content)
        global_rewrite = should_rewrite_outline_not_patch(
            goal,
            outline_excerpt=content,
            scope=scope,
        )

    plan = plan_edit_from_read_content(content, goal, global_rewrite=global_rewrite)
    if plan.get("rewrite"):
        return rev

    if plan.get("batch") and plan.get("edits"):
        rev.edits = [
            RevisionEdit.from_dict(e)
            for e in plan["edits"]
            if isinstance(e, dict) and str(e.get("old_text") or "").strip()
        ]
        return rev

    old_text = str(plan.get("old_text") or "").strip()
    if plan.get("found") and old_text:
        sl = plan.get("start_line")
        el = plan.get("end_line")
        rev.edits = [
            RevisionEdit(
                old_text=old_text,
                new_text=str(plan.get("new_text") or ""),
                start_line=int(sl) if sl is not None else None,
                end_line=int(el) if el is not None else None,
            )
        ]
    return rev


def _edit_params_executable(edit_params: dict[str, Any]) -> bool:
    if edit_params.get("edits"):
        return True
    if edit_params.get("old_text"):
        return True
    if edit_params.get("start_line") is not None:
        return True
    return False


def execute_revision_fast_path(
    state: AgentState,
    *,
    node_id: str = "revision_executor",
    edit_spec: dict[str, Any] | None = None,
    command: WritingCommand | None = None,
) -> AgentState:
    """Scoped read → optional dry_run → edit → revision_done."""
    from app.services.artifact_resolver import resolve_artifact_target
    from app.services.artifact_tools import handle_edit_text_artifact, handle_read_text_artifact
    from app.services.manuscript_service import resolve_manuscript
    from app.services.turn_event_log import record_turn_event

    payload = dict(state.get("input_payload") or {})
    spec = dict(edit_spec or {})
    revision_raw = payload.get("revision_intent")
    task_id = state["task_id"]
    target = resolve_artifact_target(
        state,
        action="edit_plot",
        target_hint=str(spec.get("target_kind") or "outline"),
    )
    filename = target.filename

    rev = RevisionIntent.from_dict(revision_raw) if isinstance(revision_raw, dict) else None
    if rev is None:
        rev = RevisionIntent(artifact_filename=filename, edits=[])
    if not rev.artifact_filename:
        rev.artifact_filename = filename

    read_params = revision_intent_to_read_params(rev, task_id)
    read_params["filename"] = filename
    if spec.get("start_line"):
        read_params["start_line"] = spec.get("start_line")
        read_params["end_line"] = spec.get("end_line")
    read_out = handle_read_text_artifact(read_params)
    state = record_turn_event(
        state,
        "scoped_read",
        node_id,
        "edit_plot",
        {"scope": read_out.get("scope")},
    )

    content = str(read_out.get("raw_content") or "")
    if rev.target_sections and not any(e.old_text or e.start_line for e in rev.edits):
        rev = enrich_revision_edits_from_sections(rev, content)

    goal = str(payload.get("goal") or payload.get("query") or "")
    if not any(e.old_text or e.start_line is not None for e in rev.edits):
        rev = _resolve_edits_from_content(rev, content, goal)

    edit_params = revision_intent_to_edit_params(rev, task_id) if rev else dict(spec)
    edit_params["task_id"] = task_id
    edit_params["filename"] = filename
    if spec.get("old_text") and not edit_params.get("old_text"):
        edit_params["old_text"] = spec.get("old_text")
        edit_params["new_text"] = spec.get("new_text", "")

    if not _edit_params_executable(edit_params):
        summary = "已读取工件，但未能定位可执行的修改范围。请补充章节名或贴出原句后再改。"
        return merge_state(
            state,
            input_payload=payload,
            tool_results=[{"tool": "read_text_artifact", "status": "ok", "result": read_out}],
            reasoning_result={
                "summary": summary,
                "confidence": 0.75,
                "risk_level": "LOW",
                "structured": {"source": "revision_fast_path_unresolved"},
            },
            status=TaskStatus.REASONED.value,
            current_node=node_id,
        )

    section_only = bool(rev and rev.target_sections and not any(e.old_text for e in rev.edits))
    needs_preview = section_only or (rev and rev.output_mode == "diff")
    tool_results: list[dict[str, Any]] = [
        {"tool": "read_text_artifact", "status": "ok", "result": read_out},
    ]
    if needs_preview:
        preview_params = {**edit_params, "dry_run": True}
        preview_out = handle_edit_text_artifact(preview_params)
        tool_results.append(
            {
                "tool": "edit_text_artifact",
                "status": "ok",
                "result": preview_out,
                "phase": "dry_run",
            }
        )
        payload["revision_dry_run_completed"] = True

    edit_params["dry_run"] = False
    edit_out = handle_edit_text_artifact(edit_params)
    if edit_out.get("status") == "error" or edit_out.get("error"):
        reason = str(edit_out.get("error") or "edit_failed")
        if command is not None:
            from app.services.writing.executor import record_command_failure

            payload = record_command_failure(payload, command, reason=reason)
        return merge_state(
            state,
            input_payload=payload,
            tool_results=[
                *tool_results,
                {"tool": "edit_text_artifact", "status": "error", "result": edit_out},
            ],
            status=TaskStatus.MISSION_RUNNING.value,
            errors=[reason],
            current_node=node_id,
        )

    from app.services.outline_steer_patch import is_outline_filename

    ms = resolve_manuscript(task_id, state.get("manuscript"))
    nbytes = int(edit_out.get("bytes") or 0)
    if is_outline_filename(filename):
        ms.outline_path = filename
        ms.outline_bytes = nbytes
    elif ms.body_path == filename or not ms.body_path:
        ms.body_path = filename
        ms.body_bytes = nbytes

    from app.services.revision_context import store_last_revision_intent
    from app.services.revision_done import mark_revision_completed
    from app.services.steer_planning_lifecycle import maybe_complete_steer_planning_after_execute
    from app.services.turn_guard import mark_turn_step_executed

    if rev:
        payload = store_last_revision_intent(payload, rev.to_dict())
    payload["revision_intent"] = rev.to_dict() if rev else revision_raw
    payload["revision_done"] = True

    updated = merge_state(
        state,
        input_payload=payload,
        tool_results=[
            *tool_results,
            {"tool": "edit_text_artifact", "status": "ok", "result": edit_out, "phase": "apply"},
        ],
        manuscript=ms.to_dict(),
        status=TaskStatus.TOOL_EXECUTED.value,
        current_node=node_id,
    )
    updated = mark_turn_step_executed(updated)
    updated = record_turn_event(
        updated,
        "scoped_edit_completed",
        node_id,
        "edit_plot",
        {"replacements": edit_out.get("replacements")},
    )
    updated = mark_revision_completed(updated)
    return maybe_complete_steer_planning_after_execute(updated)
