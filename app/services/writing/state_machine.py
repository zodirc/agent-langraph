"""Command queue and current-command binding."""

from __future__ import annotations

from typing import Any, Optional

from app.domain.writing_command import WritingCommand
from app.runtime.state import AgentState, merge_state
from app.services.writing.command_builder import build_writing_command

WRITING_COMMAND_KEY = "writing_command"
WRITING_COMMAND_QUEUE_KEY = "writing_command_queue"

COMMAND_WORK_ITEM_KINDS = frozenset(
    {"edit_plot", "review_outline", "reset_body", "write_outline", "write_body"}
)


def work_item_params_for_command(command: WritingCommand) -> dict[str, Any]:
    return {"command_id": command.command_id}


def get_current_command(payload: dict[str, Any]) -> Optional[WritingCommand]:
    raw = payload.get(WRITING_COMMAND_KEY)
    if isinstance(raw, dict) and raw.get("action"):
        return WritingCommand.from_dict(raw)
    return None


def enqueue_command(payload: dict[str, Any], command: WritingCommand) -> dict[str, Any]:
    out = dict(payload)
    queue = list(out.get(WRITING_COMMAND_QUEUE_KEY) or [])
    sig = command.signature()
    if not any(
        isinstance(row, dict) and row.get("signature") == sig and row.get("status") == "failed"
        for row in queue
    ):
        queue.append(
            {
                "command_id": command.command_id,
                "action": command.action,
                "signature": sig,
                "status": "pending",
            }
        )
    out[WRITING_COMMAND_QUEUE_KEY] = queue[-20:]
    out[WRITING_COMMAND_KEY] = command.to_dict()
    return out


def mark_command_failed(payload: dict[str, Any], command: WritingCommand, *, reason: str) -> dict[str, Any]:
    out = dict(payload)
    queue = list(out.get(WRITING_COMMAND_QUEUE_KEY) or [])
    for idx, row in enumerate(queue):
        if isinstance(row, dict) and row.get("command_id") == command.command_id:
            queue[idx] = {**row, "status": "failed", "reason": reason[:240]}
            break
    else:
        queue.append(
            {
                "command_id": command.command_id,
                "action": command.action,
                "signature": command.signature(),
                "status": "failed",
                "reason": reason[:240],
            }
        )
    out[WRITING_COMMAND_QUEUE_KEY] = queue[-20:]
    return out


def sync_work_item_command(state: AgentState, item: dict[str, Any]) -> AgentState:
    command = build_writing_command(state, item)
    payload = enqueue_command(dict(state.get("input_payload") or {}), command)
    return merge_state(state, input_payload=payload)
