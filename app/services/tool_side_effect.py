"""Tool side-effect classification for safe parallel execution (WP-4.3)."""

from __future__ import annotations

READ_ONLY_TOOLS = frozenset(
    {
        "echo",
        "calculator",
        "get_runtime_info",
        "summarize_text",
        "ls_path",
        "read_file",
        "grep_file",
        "read_text_artifact",
        "get_manuscript_context",
        "verify_backend",
        "list_task_artifacts",
    }
)

WRITE_TOOLS = frozenset(
    {
        "write_file",
        "append_file",
        "move_path",
        "copy_path",
        "replace_in_file",
        "touch_file",
        "mkdir_path",
        "rm_path",
        "edit_text_artifact",
        "write_text_artifact",
        "append_text_artifact",
        "enqueue_mission_work_item",
        "set_mission_work_plan",
    }
)


def tool_is_read_only(tool_name: str) -> bool:
    name = str(tool_name or "")
    if name in WRITE_TOOLS:
        return False
    if name in READ_ONLY_TOOLS:
        return True
    return name.startswith("read_") or name.startswith("get_") or name.startswith("list_")


def partition_stage_by_side_effect(tools: list[str]) -> list[list[str]]:
    """Split a stage so writes run alone; reads may share a parallel stage."""
    reads = [t for t in tools if tool_is_read_only(t)]
    writes = [t for t in tools if not tool_is_read_only(t)]
    stages: list[list[str]] = []
    if len(reads) > 1:
        stages.append(reads)
    elif reads:
        stages.append(reads)
    for w in writes:
        stages.append([w])
    return stages or [tools]


def normalize_stages_for_safe_parallel(stages: list[list[str]]) -> list[list[str]]:
    out: list[list[str]] = []
    for stage in stages:
        out.extend(partition_stage_by_side_effect(stage))
    return out
