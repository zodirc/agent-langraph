"""Validate WritingCommand before preview or execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.domain.writing_command import WritingCommand

REVISION_ACTIONS = frozenset({"edit_plot", "review_outline", "reset_body"})


@dataclass
class CommandValidationError:
    code: str
    message: str


def validate_target_bound(command: WritingCommand) -> Optional[CommandValidationError]:
    if not str(command.target_filename or "").strip():
        return CommandValidationError(
            "missing_target",
            f"{command.action} requires an explicit target_filename",
        )
    return None


def validate_confirmation(command: WritingCommand, payload: dict[str, Any]) -> Optional[CommandValidationError]:
    if command.confirmation_status == "rejected":
        return CommandValidationError("rejected", "Command was rejected and must not execute")
    if not command.requires_confirmation:
        return None
    if command.confirmation_status == "pending":
        if payload.get("steer_intent_pending_confirm") and not payload.get("steer_intent_confirmed"):
            return CommandValidationError(
                "confirmation_pending",
                f"Intent confirmation required before executing {command.action}",
            )
    return None


def validate_executable(
    command: WritingCommand,
    payload: dict[str, Any],
) -> Optional[CommandValidationError]:
    for check in (
        validate_target_bound(command),
        validate_confirmation(command, payload),
    ):
        if check is not None:
            return check
    return None


def command_retry_blocked(payload: dict[str, Any], command: Optional[WritingCommand] = None) -> bool:
    blocked = payload.get("command_retry_blocked")
    if not isinstance(blocked, dict) or not blocked.get("blocked"):
        return False
    if command is None:
        return True
    return str(blocked.get("signature") or "") == command.signature()
