"""
Global priority task queue for batch and scheduler workloads (Ch20).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config.settings import settings
from app.services.celery_queue import dispatch_queue_task, pop_highest_pending, push_pending
from app.services.queue_priority import effective_priority_from_row


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskQueueStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or settings.SQLITE_PATH
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_queue (
                    queue_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    input_payload TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_ref TEXT,
                    enqueued_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    task_id TEXT,
                    error TEXT
                )
                """
            )
            conn.commit()

    def enqueue(
        self,
        *,
        user_id: str,
        task_type: str,
        input_payload: dict[str, Any],
        priority: int = 0,
        source: str = "api",
        source_ref: Optional[str] = None,
    ) -> str:
        queue_id = str(uuid.uuid4())
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_queue
                (queue_id, user_id, task_type, input_payload, priority, status,
                 source, source_ref, enqueued_at)
                VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?, ?)
                """,
                (
                    queue_id,
                    user_id,
                    task_type,
                    json.dumps(input_payload),
                    int(priority),
                    source,
                    source_ref,
                    now,
                ),
            )
            conn.commit()
        push_pending(queue_id, priority=int(priority), enqueued_at=now)
        if settings.QUEUE_BACKEND.lower() == "celery":
            eff = effective_priority_from_row(
                {"priority": int(priority), "enqueued_at": now}
            )
            dispatch_queue_task(queue_id, effective=eff)
        return queue_id

    def dequeue_next(self, *, user_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        if settings.QUEUE_BACKEND.lower() == "celery" and user_id is None:
            qid = pop_highest_pending()
            if qid:
                item = self._claim_by_id(qid)
                if item:
                    return item
        with self._connect() as conn:
            if user_id:
                rows = conn.execute(
                    """
                    SELECT * FROM task_queue
                    WHERE status = 'PENDING' AND user_id = ?
                    ORDER BY priority DESC, enqueued_at ASC
                    LIMIT 50
                    """,
                    (user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM task_queue
                    WHERE status = 'PENDING'
                    ORDER BY priority DESC, enqueued_at ASC
                    LIMIT 50
                    """
                ).fetchall()
        if not rows:
            return None
        best = max(rows, key=effective_priority_from_row)
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE task_queue SET status = 'RUNNING', started_at = ?
                WHERE queue_id = ? AND status = 'PENDING'
                """,
                (now, best["queue_id"]),
            )
            conn.commit()
        return self._row_to_item(best)

    def _claim_by_id(self, queue_id: str) -> Optional[dict[str, Any]]:
        now = _now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM task_queue WHERE queue_id = ? AND status = 'PENDING'",
                (queue_id,),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                """
                UPDATE task_queue SET status = 'RUNNING', started_at = ?
                WHERE queue_id = ? AND status = 'PENDING'
                """,
                (now, queue_id),
            )
            conn.commit()
        return self._row_to_item(row)

    def mark_completed(self, queue_id: str, *, task_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE task_queue
                SET status = 'COMPLETED', task_id = ?, completed_at = ?
                WHERE queue_id = ?
                """,
                (task_id, _now_iso(), queue_id),
            )
            conn.commit()

    def mark_failed(self, queue_id: str, *, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE task_queue
                SET status = 'FAILED', error = ?, completed_at = ?
                WHERE queue_id = ?
                """,
                (error[:2000], _now_iso(), queue_id),
            )
            conn.commit()

    def pending_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM task_queue WHERE status = 'PENDING'"
            ).fetchone()
        return int(row["c"]) if row else 0

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "queue_id": row["queue_id"],
            "user_id": row["user_id"],
            "task_type": row["task_type"],
            "input_payload": json.loads(row["input_payload"]),
            "priority": row["priority"],
            "status": row["status"],
            "source": row["source"],
            "source_ref": row["source_ref"],
            "enqueued_at": row["enqueued_at"],
        }


_store: TaskQueueStore | None = None


def get_task_queue_store() -> TaskQueueStore:
    global _store
    if _store is None:
        _store = TaskQueueStore()
    return _store
