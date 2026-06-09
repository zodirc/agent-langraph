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


def stream_edit_diff_preview(
    *,
    task_id: str,
    filename: str,
    diff_preview: str,
    node: str = "artifact_edit",
) -> None:
    """Push edit diff to SSE and persist on progress for confirmation gates."""
    text = (diff_preview or "").strip()
    if not text:
        return
    try:
        from app.services.stream_progress import report_writing_delta

        report_writing_delta(
            node=node,
            phase="diff_preview",
            text=text,
            filename=filename,
        )
    except Exception:
        pass
    try:
        from app.runtime.state_field_access import progress_from_state, set_progress_on_state
        from app.services.state_store import get_state_store

        stored = get_state_store().load(task_id) or {}
        progress = dict(progress_from_state(stored) or {})
        previews = list(progress.get("edit_diff_previews") or [])
        previews.append(
            {
                "filename": filename,
                "diff_preview": text[:8000],
                "node": node,
            }
        )
        progress["edit_diff_previews"] = previews[-6:]
        progress["last_edit_diff_preview"] = text[:8000]
        payload = dict(stored.get("input_payload") or {})
        get_state_store().save(
            set_progress_on_state(
                {**stored, "task_id": task_id, "input_payload": payload},
                progress,
            )
        )
    except Exception:
        pass
