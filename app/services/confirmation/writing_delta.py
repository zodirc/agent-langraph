"""Track per-step writing deltas for outcome confirmation gates."""

from __future__ import annotations

from typing import Any, Optional

from app.config.settings import settings
from app.services.confirmation.config import load_confirmation_gates_config


def record_writing_step_start(
    state: dict[str, Any],
    *,
    filename: str,
    action: str,
    work_item_id: Optional[str] = None,
) -> dict[str, Any]:
    """Snapshot byte offset at step start for delta extraction."""
    from app.services.artifact_tools import task_artifact_dir
    from app.services.manuscript_service import sanitize_artifact_basename

    task_id = str(state["task_id"])
    resolved = sanitize_artifact_basename(filename)
    path = task_artifact_dir(task_id) / resolved
    start_bytes = path.stat().st_size if path.exists() else 0
    return {
        "filename": filename,
        "action": action,
        "work_item_id": work_item_id,
        "start_bytes": start_bytes,
        "excerpt": "",
        "bytes_added": 0,
    }


def finalize_writing_step_delta(
    state: dict[str, Any],
    delta: dict[str, Any],
) -> dict[str, Any]:
    """Read artifact tail since step start and store excerpt on progress."""
    from app.services.artifact_tools import read_artifact_tail, task_artifact_dir
    from app.services.manuscript_service import sanitize_artifact_basename

    cfg = load_confirmation_gates_config()
    task_id = str(state["task_id"])
    filename = str(delta.get("filename") or "")
    if not filename:
        return delta

    resolved = sanitize_artifact_basename(filename)
    path = task_artifact_dir(task_id) / resolved
    end_bytes = path.stat().st_size if path.exists() else 0
    start_bytes = int(delta.get("start_bytes") or 0)
    added = max(0, end_bytes - start_bytes)

    excerpt = ""
    if added > 0:
        tail = read_artifact_tail(task_id, resolved, max_chars=cfg.delta_max_chars)
        excerpt = tail.strip()

    out = {
        **delta,
        "bytes_added": added,
        "excerpt": excerpt[: cfg.delta_max_chars],
    }
    return out


def persist_writing_delta(state: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    """Merge delta into progress.writing_step_delta."""
    progress = dict(state.get("progress") or {})
    progress["writing_step_delta"] = delta
    return progress
