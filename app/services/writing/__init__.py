"""Command-driven writing pipeline: Intent → Command → Confirmation → Executor → Tools."""

from app.domain.writing_intent_model import IntentAnchor, WritingIntentRecord
from app.services.writing.command_builder import COMMAND_ACTIONS, build_from_intent, build_writing_command
from app.services.writing.command_intent import command_to_writing_intent
from app.services.writing.command_validator import (
    CommandValidationError,
    command_retry_blocked,
    validate_confirmation,
    validate_executable,
    validate_target_bound,
)
from app.services.writing.confirmation_service import StateSnapshot, build_state_snapshot, resolve_command_preview
from app.services.writing.executor import TOOL_ACTIONS, block_command_execution, execute_writing_command, record_command_failure
from app.services.writing.intent_parser import (
    parse_intent_from_intervention,
    parse_intent_from_payload,
    parse_intent_from_planning,
    store_intent_on_payload,
)
from app.services.writing.state_machine import (
    COMMAND_WORK_ITEM_KINDS,
    WRITING_COMMAND_KEY,
    WRITING_COMMAND_QUEUE_KEY,
    enqueue_command,
    get_current_command,
    sync_work_item_command,
    work_item_params_for_command,
)

__all__ = [
    "COMMAND_ACTIONS",
    "COMMAND_WORK_ITEM_KINDS",
    "CommandValidationError",
    "IntentAnchor",
    "StateSnapshot",
    "TOOL_ACTIONS",
    "WRITING_COMMAND_KEY",
    "WRITING_COMMAND_QUEUE_KEY",
    "WritingIntentRecord",
    "block_command_execution",
    "build_from_intent",
    "build_state_snapshot",
    "build_writing_command",
    "command_retry_blocked",
    "command_to_writing_intent",
    "enqueue_command",
    "execute_writing_command",
    "get_current_command",
    "parse_intent_from_intervention",
    "parse_intent_from_payload",
    "parse_intent_from_planning",
    "record_command_failure",
    "resolve_command_preview",
    "store_intent_on_payload",
    "sync_work_item_command",
    "validate_confirmation",
    "validate_executable",
    "validate_target_bound",
    "work_item_params_for_command",
]
