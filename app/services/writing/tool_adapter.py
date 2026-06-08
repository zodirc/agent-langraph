"""Map WritingCommand to tool routing at execution time only."""

from __future__ import annotations

from app.domain.writing_command import WritingCommand


def tools_for_command(command: WritingCommand) -> list[str]:
    """Return tool names only; filenames resolved at execution boundary."""
    if command.action == "review_outline":
        return ["read_text_artifact"]
    if command.action == "edit_plot":
        return ["read_text_artifact", "edit_text_artifact"]
    return []


def tool_stages_for_command(command: WritingCommand) -> list[list[str]] | None:
    if command.action != "edit_plot":
        return None
    if command.edit_spec.get("old_text"):
        return None
    from app.services.outline_steer_patch import is_outline_filename

    if is_outline_filename(command.target_filename):
        return [["read_text_artifact"], ["edit_text_artifact"]]
    return None
