"""Execute approved WritingCommand instances."""

from __future__ import annotations

from typing import Any

from app.domain.writing_command import WritingCommand
from app.runtime.state import AgentState, TaskStatus, merge_state
from app.services.writing.command_intent import command_to_writing_intent
from app.services.writing.command_validator import CommandValidationError, validate_executable
from app.services.writing.state_machine import mark_command_failed

TOOL_ACTIONS = frozenset({"edit_plot", "review_outline"})
SUBGRAPH_ACTIONS = frozenset({"write_outline", "write_body", "reset_body"})


def block_command_execution(
    state: AgentState,
    error: CommandValidationError,
) -> AgentState:
    from app.services.mission_steer_confirm import (
        attach_steer_confirmation_to_state,
        steer_confirmation_pending,
    )
    from app.services.mission_steer_outcome_confirm import (
        attach_steer_outcome_confirmation_to_state,
        steer_outcome_confirmation_pending,
    )

    payload = dict(state.get("input_payload") or {})
    if steer_outcome_confirmation_pending(payload) and not payload.get("steer_outcome_confirmed"):
        return attach_steer_outcome_confirmation_to_state(
            merge_state(
                state,
                current_node="mission_act",
                status=state.get("status") or TaskStatus.MISSION_RUNNING.value,
            )
        )
    if steer_confirmation_pending(payload) and not payload.get("steer_intent_confirmed"):
        return attach_steer_confirmation_to_state(
            merge_state(
                state,
                current_node="mission_act",
                status=state.get("status") or TaskStatus.MISSION_RUNNING.value,
            )
        )
    return merge_state(
        state,
        current_node="mission_act",
        status=TaskStatus.MISSION_RUNNING.value,
        errors=[error.message],
        reasoning_result={
            "summary": error.message,
            "confidence": 0.9,
            "risk_level": "LOW",
            "structured": {"source": "writing_command_blocked", "code": error.code},
        },
    )


def record_command_failure(
    payload: dict[str, Any],
    command: WritingCommand,
    *,
    reason: str,
) -> dict[str, Any]:
    out = mark_command_failed(payload, command, reason=reason)
    out["command_retry_blocked"] = {
        "blocked": True,
        "signature": command.signature(),
        "command_id": command.command_id,
        "reason": reason[:240],
    }
    out["writing_command"] = command.to_dict()
    return out


def _bind_command_payload(payload: dict[str, Any], command: WritingCommand) -> dict[str, Any]:
    from app.services.writing.tool_adapter import tool_stages_for_command, tools_for_command

    out = dict(payload)
    out["writing_command"] = command.to_dict()
    out["writing_intent"] = command_to_writing_intent(
        command,
        mission_step=int(out.get("mission_step") or 1),
    )
    tools = tools_for_command(command)
    if tools:
        out["selected_tools"] = tools
        stages = tool_stages_for_command(command)
        if stages:
            out["tool_stages"] = stages
    return out


def _execute_edit_plot(state: AgentState, command: WritingCommand) -> AgentState:
    from app.services.artifact_resolver import resolve_artifact_target

    outline_edit = None
    if outline_edit is not None:
        return outline_edit  # type: ignore[return-value]

    spec = dict(command.edit_spec)
    payload = dict(state.get("input_payload") or {})
    target = resolve_artifact_target(state, action="edit_plot", target_hint=command.target_kind)
    filename = target.filename

    revision_raw = payload.get("revision_intent")
    if revision_raw or spec.get("edits") or spec.get("start_line"):
        from app.services.writing.revision_executor import execute_revision_fast_path

        return execute_revision_fast_path(
            state,
            node_id="writing_executor",
            edit_spec=spec,
            command=command,
        )

    if spec.get("old_text"):
        from app.services.artifact_tools import handle_edit_text_artifact, handle_read_text_artifact
        from app.services.manuscript_service import resolve_manuscript

        task_id = state["task_id"]
        read_out = handle_read_text_artifact(
            {
                "task_id": task_id,
                "filename": filename,
                "max_chars": 12000,
                "use_cache": True,
                "with_line_numbers": bool(spec.get("start_line") or spec.get("end_line")),
            }
        )
        edit_out = handle_edit_text_artifact(
            {
                "task_id": task_id,
                "filename": filename,
                "old_text": str(spec.get("old_text")),
                "new_text": str(spec.get("new_text", "")),
                "replace_all": bool(spec.get("replace_all", False)),
                "occurrence_index": spec.get("occurrence_index"),
                "start_line": spec.get("start_line"),
                "end_line": spec.get("end_line"),
                "dry_run": bool(spec.get("dry_run", False)),
            }
        )
        if edit_out.get("status") == "error" or edit_out.get("error"):
            reason = str(edit_out.get("error") or "edit_failed")
            payload = record_command_failure(payload, command, reason=reason)
            return merge_state(
                state,
                input_payload=payload,
                tool_results=[
                    {"tool": "read_text_artifact", "status": "ok", "result": read_out},
                    {"tool": "edit_text_artifact", "status": "error", "result": edit_out},
                ],
                status=TaskStatus.MISSION_RUNNING.value,
                errors=[reason],
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
        from app.services.steer_planning_lifecycle import maybe_complete_steer_planning_after_execute
        from app.services.turn_guard import mark_turn_step_executed

        updated = merge_state(
            state,
            tool_results=[
                {"tool": "read_text_artifact", "status": "ok", "result": read_out},
                {"tool": "edit_text_artifact", "status": "ok", "result": edit_out},
            ],
            manuscript=ms.to_dict(),
            status=TaskStatus.TOOL_EXECUTED.value,
        )
        updated = mark_turn_step_executed(updated)
        return maybe_complete_steer_planning_after_execute(updated)

    from app.nodes.tool_node import tool_execution_node

    return tool_execution_node(state)


def execute_writing_command(
    state: AgentState,
    command: WritingCommand,
    *,
    step_decision: dict[str, Any],
    mission_act_for_writing: Any,
    oma_act_available: Any,
) -> AgentState:
    payload = dict(state.get("input_payload") or {})
    error = validate_executable(command, payload)
    if error is not None:
        return block_command_execution(state, error)

    payload = _bind_command_payload(payload, command)
    state = merge_state(state, input_payload=payload)

    if command.action == "edit_plot":
        return _execute_edit_plot(state, command)

    if command.action == "review_outline":
        from app.nodes.tool_node import tool_execution_node

        return tool_execution_node(state)

    if command.action in SUBGRAPH_ACTIONS:
        return mission_act_for_writing(
            state,
            step_decision,
            allow_pipeline=not oma_act_available(state),
        )

    return mission_act_for_writing(
        state,
        step_decision,
        allow_pipeline=not oma_act_available(state),
    )
