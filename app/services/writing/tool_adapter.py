"""Map WritingCommand to tool calls at execution time only."""

from __future__ import annotations

from typing import Any

from app.config.settings import settings
from app.domain.writing_command import WritingCommand


def tools_for_command(command: WritingCommand) -> tuple[list[str], dict[str, Any]]:
    """Return (selected_tools, tool_params) derived solely from the command."""
    action = command.action
    filename = command.target_filename
    params: dict[str, Any] = {}

    if action == "review_outline":
        return (
            ["read_text_artifact"],
            {
                "read_text_artifact": {
                    "filename": filename,
                    "max_chars": int(getattr(settings, "MISSION_OUTLINE_MAX_CHARS", 12000)),
                }
            },
        )

    if action != "edit_plot":
        return [], {}

    spec = dict(command.edit_spec or {})
    read_params = {
        "filename": filename,
        "max_chars": int(spec.get("read_max_chars", 12000)),
    }
    params["read_text_artifact"] = read_params

    if spec.get("old_text"):
        edit_params = {
            k: spec[k]
            for k in (
                "filename",
                "old_text",
                "new_text",
                "replace_all",
                "occurrence_index",
                "start_line",
                "end_line",
                "dry_run",
            )
            if k in spec
        }
        edit_params.setdefault("filename", filename)
        params["edit_text_artifact"] = edit_params

    return ["read_text_artifact", "edit_text_artifact"], params


def tool_stages_for_command(command: WritingCommand) -> list[list[str]] | None:
    if command.action != "edit_plot":
        return None
    if command.edit_spec.get("old_text"):
        return None
    from app.services.outline_steer_patch import is_outline_filename

    if is_outline_filename(command.target_filename):
        return [["read_text_artifact"], ["edit_text_artifact"]]
    return None
