"""Map WritingCommand to runtime writing_intent (executor-only)."""

from __future__ import annotations

from app.config.settings import settings
from app.domain.writing_command import WritingCommand

_WRITE_ENABLED = frozenset({"write_outline", "write_body", "reset_body"})


def command_to_writing_intent(command: WritingCommand, *, mission_step: int) -> dict:
    base = {
        "source": "writing_command",
        "mission_step": mission_step,
        "command_id": command.command_id,
        "target_filename": command.target_filename,
    }
    action = command.action
    write_spec = dict(command.write_spec or {})

    if action == "edit_plot":
        return {**base, "enabled": False, "action": "edit_plot"}
    if action == "review_outline":
        return {**base, "enabled": False, "action": "review_outline"}
    if action == "write_outline":
        return {
            **base,
            "enabled": True,
            "action": "write_outline",
            "target_chars": int(write_spec.get("outline_max_chars") or getattr(settings, "MISSION_OUTLINE_MAX_CHARS", 12000)),
            "min_chars": int(getattr(settings, "MANUSCRIPT_MIN_OUTLINE_CHARS", 80)),
            "require_read_first": bool(write_spec.get("require_read_first", True)),
        }
    if action == "write_body":
        return {
            **base,
            "enabled": True,
            "action": "write_body",
            "target_chars": int(write_spec.get("target_chars") or 3500),
            "min_chars": int(getattr(settings, "MANUSCRIPT_MIN_BODY_CHARS", 200)),
            "chapter_index": 1,
            "require_read_first": False,
        }
    if action == "reset_body":
        return {
            **base,
            "enabled": True,
            "action": "reset_body",
            "target_chars": int(write_spec.get("target_chars") or 3500),
            "min_chars": int(getattr(settings, "MANUSCRIPT_MIN_BODY_CHARS", 200)),
            "chapter_index": 1,
            "require_read_first": bool(write_spec.get("require_read_first", True)),
        }
    return {**base, "enabled": action in _WRITE_ENABLED, "action": action}
