"""Artifact snapshots for diff-based outcome previews."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any, Optional

from app.services.manuscript_service import sanitize_artifact_basename


def snapshot_dir(task_id: str) -> Path:
    from app.services.artifact_tools import task_artifact_dir

    return task_artifact_dir(task_id) / ".snapshots"


def snapshot_path(task_id: str, snapshot_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in snapshot_id)
    return snapshot_dir(task_id) / f"{safe}.txt"


def save_artifact_snapshot(
    task_id: str,
    filename: str,
    snapshot_id: str,
    *,
    state: Optional[dict[str, Any]] = None,
) -> bool:
    """Copy artifact content to .snapshots/{id}.txt before destructive edit."""
    from app.services.artifact_tools import task_artifact_dir

    resolved = sanitize_artifact_basename(filename)
    src = task_artifact_dir(task_id) / resolved
    if not src.exists():
        return False
    dest_dir = snapshot_dir(task_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = snapshot_path(task_id, snapshot_id)
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return True


def read_snapshot(task_id: str, snapshot_id: str) -> str:
    path = snapshot_path(task_id, snapshot_id)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def unified_diff(before: str, after: str, *, filename: str = "artifact") -> str:
    if before == after:
        return ""
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"{filename} (before)",
        tofile=f"{filename} (after)",
        lineterm="",
    )
    text = "".join(lines)
    return text.rstrip()
