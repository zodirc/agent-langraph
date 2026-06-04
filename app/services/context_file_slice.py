"""On-demand file slices for code-agent context (ADR §10.3)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.context_items import ContextItem, new_context_id


def read_file_slice(
    path: str | Path,
    *,
    start_line: int = 1,
    max_lines: int = 120,
    max_chars: int = 6000,
) -> str:
    """
    Read a local workspace file window (never whole large files).
    """
    p = Path(path)
    if not p.is_file():
        return ""
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    start = max(1, start_line)
    window = lines[start - 1 : start - 1 + max_lines]
    text = "\n".join(window)
    if len(text) > max_chars:
        return text[:max_chars] + "\n…(slice truncated)"
    return text


def expand_file_context_item(
    item: ContextItem,
    *,
    task_id: str,
    extra_lines: int = 40,
) -> ContextItem | None:
    """
    On-demand expansion: widen slice around path in meta when content was truncated.
    """
    path = item.meta.get("path") or item.meta.get("file")
    if not path or not task_id:
        return None
    from app.services.artifact_tools import task_artifact_dir

    full = task_artifact_dir(task_id) / str(path)
    if not full.is_file():
        return None
    expanded = read_file_slice(full, max_lines=extra_lines + 40, max_chars=8000)
    if not expanded or expanded == item.content:
        return None
    return ContextItem(
        id=item.id,
        kind=item.kind,
        source=item.source,
        role=item.role,
        content=expanded,
        priority=item.priority,
        freshness=item.freshness,
        estimated_tokens=0,
        compressible=item.compressible,
        droppable=item.droppable,
        bucket=item.bucket,
        meta={**item.meta, "expanded": True},
    )


def file_slice_item_from_path(
    path: str,
    *,
    task_id: str = "",
    start_line: int = 1,
    priority: str = "high",
) -> ContextItem | None:
    resolved = path
    if task_id:
        from app.services.artifact_tools import task_artifact_dir

        candidate = task_artifact_dir(task_id) / path
        if candidate.is_file():
            resolved = str(candidate)
    content = read_file_slice(resolved, start_line=start_line)
    if not content:
        return None
    return ContextItem(
        id=new_context_id("fs"),
        kind="file_slice",
        source="workspace",
        role="system",
        content=f"[{path}]\n{content}",
        priority=priority,  # type: ignore[arg-type]
        bucket="file_context",
        meta={"path": path, "start_line": start_line},
    )
