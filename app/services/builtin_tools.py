from __future__ import annotations

from typing import Any

from app.services.artifact_tools import (
    handle_append_text_artifact,
    handle_calculator,
    handle_edit_text_artifact,
    handle_get_runtime_info,
    handle_read_text_artifact,
    handle_write_text_artifact,
)
from app.services.manuscript_context import analyze_manuscript_structure
from app.services.tool_registry import ToolRegistry, ToolSpec


def register_builtin_tools(registry: ToolRegistry) -> None:
    """Register all built-in tools (called from ToolRegistry.__init__)."""
    _register_core(registry)
    _register_artifact_and_utility(registry)


def _register_core(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="echo",
            description="Echo a message for debugging",
            input_schema={"type": "object", "properties": {"message": {"type": "string"}}},
            output_schema={"type": "object"},
            required_role="user",
            risk_level="LOW",
            handler=lambda p: {"echo": p.get("message", ""), "status": "ok"},
        )
    )
    registry.register(
        ToolSpec(
            name="summarize_text",
            description="Summarize provided text",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
            output_schema={"type": "object"},
            required_role="user",
            risk_level="LOW",
            handler=_summarize_handler,
        )
    )


def _manuscript_context_handler(params: dict[str, Any]) -> dict[str, Any]:
    task_id = str(params["task_id"])
    body = str(params.get("body_filename") or "novel.txt")
    outline = params.get("outline_filename")
    analysis = analyze_manuscript_structure(
        task_id,
        body_path=body,
        outline_path=str(outline) if outline else None,
    )
    return {"status": "ok", **analysis}


def _summarize_handler(params: dict[str, Any]) -> dict[str, Any]:
    text = str(params.get("text", "")).strip()
    if len(text) <= 120:
        summary = text
    else:
        summary = text[:117] + "..."
    return {"summary": summary, "length": len(text), "status": "ok"}


def _register_artifact_and_utility(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="get_runtime_info",
            description=(
                "Return actual model name, capabilities, and limits. "
                "Use for questions about what model you are, web access, or features."
            ),
            input_schema={
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
            },
            output_schema={"type": "object"},
            required_role="user",
            risk_level="LOW",
            handler=handle_get_runtime_info,
        )
    )
    registry.register(
        ToolSpec(
            name="calculator",
            description="Evaluate a safe arithmetic expression (+ - * / parentheses)",
            input_schema={
                "type": "object",
                "properties": {"expression": {"type": "string"}},
            },
            output_schema={"type": "object"},
            required_role="user",
            risk_level="LOW",
            handler=handle_calculator,
        )
    )
    registry.register(
        ToolSpec(
            name="write_text_artifact",
            description="Write text to a task artifact file (.md .txt .json .csv)",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "filename": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
            output_schema={"type": "object"},
            required_role="user",
            risk_level="LOW",
            handler=handle_write_text_artifact,
        )
    )
    registry.register(
        ToolSpec(
            name="append_text_artifact",
            description="Append text to an existing task artifact (for long documents chapter by chapter)",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "filename": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
            output_schema={"type": "object"},
            required_role="user",
            risk_level="LOW",
            handler=handle_append_text_artifact,
        )
    )
    registry.register(
        ToolSpec(
            name="get_manuscript_context",
            description=(
                "Analyze long-form manuscript: chapter boundaries, tail excerpt, "
                "next chapter outline slice. Use before planning append_body."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "body_filename": {"type": "string"},
                    "outline_filename": {"type": "string"},
                },
            },
            output_schema={"type": "object"},
            required_role="user",
            risk_level="LOW",
            handler=_manuscript_context_handler,
        )
    )
    registry.register(
        ToolSpec(
            name="read_text_artifact",
            description="Read a previously written task artifact file",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "filename": {"type": "string"},
                    "max_chars": {"type": "integer"},
                },
            },
            output_schema={"type": "object"},
            required_role="user",
            risk_level="LOW",
            handler=handle_read_text_artifact,
        )
    )
    registry.register(
        ToolSpec(
            name="edit_text_artifact",
            description=(
                "Safely edit an existing task artifact by replacing known text. "
                "Only task artifact files are allowed and every edit is audited."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "filename": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                    "occurrence_index": {"type": "integer"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "dry_run": {"type": "boolean"},
                    "user_role": {"type": "string"},
                },
            },
            output_schema={"type": "object"},
            required_role="admin",
            risk_level="LOW",
            handler=handle_edit_text_artifact,
        )
    )
    from app.services.mission_tools import (
        handle_enqueue_mission_work_item,
        handle_set_mission_work_plan,
    )

    registry.register(
        ToolSpec(
            name="enqueue_mission_work_item",
            description=(
                "Append work items to an orchestrated mission plan "
                "(e.g. append_chapter, edit_plot, human_gate). "
                "Use instead of hard-coded decomposition."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "items": {"type": "array"},
                    "work_item": {"type": "object"},
                },
            },
            output_schema={"type": "object"},
            required_role="user",
            risk_level="LOW",
            handler=handle_enqueue_mission_work_item,
        )
    )
    registry.register(
        ToolSpec(
            name="set_mission_work_plan",
            description=(
                "Replace the mission work plan with an explicit item list "
                "(planning LLM decomposition)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "work_plan": {"type": "object"},
                },
            },
            output_schema={"type": "object"},
            required_role="user",
            risk_level="LOW",
            handler=handle_set_mission_work_plan,
        )
    )
