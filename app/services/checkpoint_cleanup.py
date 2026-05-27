"""LangGraph checkpoint retention cleanup."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config.settings import settings
from app.services.db import postgres_connection, uses_postgres

logger = logging.getLogger(__name__)


def cleanup_old_checkpoints(*, retention_days: int | None = None) -> int:
    """
    Remove checkpoint rows for completed tasks older than retention_days.
    Returns number of checkpoint thread_ids removed (approximate).
    """
    days = retention_days if retention_days is not None else settings.CHECKPOINT_RETENTION_DAYS
    if days <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    if settings.CHECKPOINT_BACKEND == "postgres" and uses_postgres():
        return _cleanup_postgres(cutoff)
    return _cleanup_sqlite(cutoff)


def _completed_task_ids_before(cutoff_iso: str) -> list[str]:
    """Task IDs that completed before cutoff (used as LangGraph thread_ids)."""
    ids: list[str] = []
    if uses_postgres():
        with postgres_connection() as conn:
            rows = conn.execute(
                """
                SELECT task_id FROM task_states
                WHERE status IN ('COMPLETED', 'REJECTED', 'FAILED')
                  AND updated_at < %s
                """,
                (cutoff_iso,),
            ).fetchall()
            ids = [str(r["task_id"]) for r in rows]
    else:
        db_path = Path(settings.SQLITE_PATH)
        if not db_path.exists():
            return []
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT task_id FROM task_states
                WHERE status IN ('COMPLETED', 'REJECTED', 'FAILED')
                  AND updated_at < ?
                """,
                (cutoff_iso,),
            ).fetchall()
            ids = [str(r["task_id"]) for r in rows]
        finally:
            conn.close()
    return ids


def _cleanup_sqlite(cutoff_iso: str) -> int:
    task_ids = _completed_task_ids_before(cutoff_iso)
    if not task_ids:
        return 0
    db_path = Path(settings.CHECKPOINT_SQLITE_PATH)
    if not db_path.exists():
        return 0
    removed = 0
    conn = sqlite3.connect(str(db_path))
    try:
        for tid in task_ids:
            cur = conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (tid,))
            removed += cur.rowcount
            conn.execute("DELETE FROM checkpoint_writes WHERE thread_id = ?", (tid,))
        conn.commit()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).lower():
            logger.warning("Checkpoint sqlite cleanup: %s", exc)
    finally:
        conn.close()
    logger.info("Checkpoint cleanup (sqlite): removed ~%s rows for %s tasks", removed, len(task_ids))
    return removed


def _cleanup_postgres(cutoff_iso: str) -> int:
    task_ids = _completed_task_ids_before(cutoff_iso)
    if not task_ids:
        return 0
    removed = 0
    try:
        with postgres_connection() as conn:
            for tid in task_ids:
                r1 = conn.execute(
                    "DELETE FROM checkpoints WHERE thread_id = %s",
                    (tid,),
                )
                r2 = conn.execute(
                    "DELETE FROM checkpoint_writes WHERE thread_id = %s",
                    (tid,),
                )
                removed += getattr(r1, "rowcount", 0) + getattr(r2, "rowcount", 0)
    except Exception as exc:
        logger.warning("Checkpoint postgres cleanup: %s", exc)
        return 0
    logger.info("Checkpoint cleanup (postgres): removed ~%s rows for %s tasks", removed, len(task_ids))
    return removed
