"""Purge all persisted data for a deleted task/session (artifacts, checkpoints, audit, memory)."""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any

from app.config.settings import settings
from app.services.artifact_tools import delete_task_artifact_dir
from app.services.checkpoint_recovery import reset_thread

logger = logging.getLogger(__name__)


def _discover_checkpoint_thread_ids(task_id: str, max_turn_hint: int) -> list[str]:
    """Union of expected turn threads and any threads found in checkpoint storage."""
    found: set[str] = {task_id}
    upper = max(1, int(max_turn_hint or 1)) + 4
    for turn in range(1, upper + 1):
        found.add(f"{task_id}:t{turn}")
    found.update(_scan_checkpoint_thread_ids(task_id))
    return sorted(found)


def _scan_checkpoint_thread_ids(task_id: str) -> set[str]:
    safe = re.sub(r"[^a-zA-Z0-9\-_]", "", task_id)
    if not safe:
        return set()
    try:
        if settings.CHECKPOINT_BACKEND == "postgres":
            return _scan_checkpoint_threads_postgres(safe)
        return _scan_checkpoint_threads_sqlite(safe)
    except Exception as exc:
        logger.warning("checkpoint thread scan failed for %s: %s", task_id, exc)
        return set()


def _scan_checkpoint_threads_sqlite(task_id: str) -> set[str]:
    import sqlite3
    from pathlib import Path

    db_path = Path(settings.CHECKPOINT_SQLITE_PATH)
    if not db_path.is_file():
        return set()
    like = f"{task_id}:t%"
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT thread_id FROM checkpoints
            WHERE thread_id = ? OR thread_id LIKE ?
            UNION
            SELECT DISTINCT thread_id FROM writes
            WHERE thread_id = ? OR thread_id LIKE ?
            """,
            (task_id, like, task_id, like),
        ).fetchall()
    return {str(r[0]) for r in rows if r and r[0]}


def _scan_checkpoint_threads_postgres(task_id: str) -> set[str]:
    from app.services.db import postgres_connection

    like = f"{task_id}:t%"
    with postgres_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT thread_id FROM checkpoints
            WHERE thread_id = %s OR thread_id LIKE %s
            UNION
            SELECT DISTINCT thread_id FROM writes
            WHERE thread_id = %s OR thread_id LIKE %s
            """,
            (task_id, like, task_id, like),
        ).fetchall()
    return {str(r["thread_id"] if hasattr(r, "keys") else r[0]) for r in rows if r}


def delete_checkpoints_for_task(task_id: str, *, max_turn_hint: int = 1) -> int:
    """Best-effort delete LangGraph checkpoint threads; returns count attempted with success."""
    removed = 0
    for thread_id in _discover_checkpoint_thread_ids(task_id, max_turn_hint):
        if reset_thread(thread_id, keep_audit=False, reason="task_deleted"):
            removed += 1
    return removed


def purge_task_remains(task_id: str, *, session_turn_hint: int = 1) -> dict[str, Any]:
    """
    Remove on-disk and DB remnants after task_states row is deleted.
    session_id is the same as task_id in this runtime.
    """
    from app.services.audit_store import get_audit_store
    from app.services.llm_interaction_store import get_llm_interaction_store
    from app.services.memory_store import get_memory_store

    return {
        "artifacts_dir": delete_task_artifact_dir(task_id),
        "audit_events_removed": get_audit_store().delete_events_for_task(task_id),
        "llm_interactions_removed": get_llm_interaction_store().delete_for_task(task_id),
        "memories_removed": get_memory_store().delete_for_session(task_id),
        "checkpoint_threads_removed": delete_checkpoints_for_task(
            task_id, max_turn_hint=session_turn_hint
        ),
    }
