from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config.settings import settings


class BatchStore:
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
                CREATE TABLE IF NOT EXISTS batches (
                    batch_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    total INTEGER NOT NULL,
                    completed INTEGER NOT NULL,
                    failed INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS batch_items (
                    item_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    task_id TEXT,
                    task_type TEXT NOT NULL,
                    input_payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    position INTEGER NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    enqueued_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._ensure_column(conn, "batch_items", "priority", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "batch_items", "enqueued_at", "TEXT NOT NULL DEFAULT ''")
            conn.commit()

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        ddl: str,
    ) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name in columns:
            return
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")

    def create_batch(
        self,
        *,
        user_id: str,
        items: list[dict[str, Any]],
    ) -> tuple[str, list[str]]:
        batch_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        item_ids: list[str] = []
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO batches (batch_id, user_id, status, total, completed, failed, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (batch_id, user_id, "PENDING", len(items), 0, 0, now, now),
            )
            for position, item in enumerate(items):
                item_id = str(uuid.uuid4())
                item_ids.append(item_id)
                priority = int(item.get("priority", 0))
                conn.execute(
                    """
                    INSERT INTO batch_items
                    (item_id, batch_id, task_id, task_type, input_payload, status, error,
                     position, priority, enqueued_at)
                    VALUES (?, ?, NULL, ?, ?, ?, NULL, ?, ?, ?)
                    """,
                    (
                        item_id,
                        batch_id,
                        str(item.get("task_type", "qa")),
                        json.dumps(item.get("input_payload", {})),
                        "PENDING",
                        position,
                        priority,
                        now,
                    ),
                )
            conn.commit()
        return batch_id, item_ids

    def list_pending_items(self, batch_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM batch_items
                WHERE batch_id = ? AND status = 'PENDING'
                ORDER BY priority DESC, position ASC
                """,
                (batch_id,),
            ).fetchall()
        items = [self._row_to_item(row) for row in rows]
        return self._apply_starvation_boost(items)

    @staticmethod
    def _apply_starvation_boost(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        starvation_sec = int(getattr(settings, "QUEUE_STARVATION_SEC", 300))
        max_boost = int(getattr(settings, "QUEUE_STARVATION_MAX_BOOST", 3))
        if starvation_sec <= 0:
            return items
        now = datetime.now(timezone.utc)
        boosted: list[dict[str, Any]] = []
        for item in items:
            copy = dict(item)
            enqueued_raw = str(copy.get("enqueued_at") or "")
            if enqueued_raw:
                try:
                    enq = datetime.fromisoformat(enqueued_raw.replace("Z", "+00:00"))
                    if enq.tzinfo is None:
                        enq = enq.replace(tzinfo=timezone.utc)
                    age = (now - enq).total_seconds()
                    copy["effective_priority"] = int(copy.get("priority", 0)) + min(
                        int(age // starvation_sec), max_boost
                    )
                except ValueError:
                    copy["effective_priority"] = int(copy.get("priority", 0))
            else:
                copy["effective_priority"] = int(copy.get("priority", 0))
            boosted.append(copy)
        boosted.sort(
            key=lambda x: (-int(x.get("effective_priority", 0)), int(x.get("position", 0)))
        )
        return boosted

    def update_item(
        self,
        item_id: str,
        *,
        status: str,
        task_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE batch_items
                SET status = ?, task_id = COALESCE(?, task_id), error = ?
                WHERE item_id = ?
                """,
                (status, task_id, error, item_id),
            )
            conn.commit()

    def set_batch_status(self, batch_id: str, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE batches SET status = ?, updated_at = ? WHERE batch_id = ?",
                (status, now, batch_id),
            )
            conn.commit()

    def refresh_batch_counters(self, batch_id: str) -> None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) AS completed,
                    SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END) AS pending
                FROM batch_items WHERE batch_id = ?
                """,
                (batch_id,),
            ).fetchone()
            total = int(row["total"])
            completed = int(row["completed"] or 0)
            failed = int(row["failed"] or 0)
            pending = int(row["pending"] or 0)
            if pending > 0 and completed + failed > 0:
                status = "RUNNING"
            elif pending == 0 and failed == 0:
                status = "COMPLETED"
            elif pending == 0 and completed == 0:
                status = "FAILED"
            elif pending == 0:
                status = "PARTIAL"
            else:
                status = "PENDING"
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                UPDATE batches
                SET status = ?, completed = ?, failed = ?, updated_at = ?
                WHERE batch_id = ?
                """,
                (status, completed, failed, now, batch_id),
            )
            conn.commit()

    def get_batch(self, batch_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            batch = conn.execute("SELECT * FROM batches WHERE batch_id = ?", (batch_id,)).fetchone()
            if not batch:
                return None
            items = conn.execute(
                "SELECT * FROM batch_items WHERE batch_id = ? ORDER BY position ASC",
                (batch_id,),
            ).fetchall()
        return {
            "batch_id": batch["batch_id"],
            "user_id": batch["user_id"],
            "status": batch["status"],
            "total": batch["total"],
            "completed": batch["completed"],
            "failed": batch["failed"],
            "created_at": batch["created_at"],
            "updated_at": batch["updated_at"],
            "items": [self._row_to_item(row) for row in items],
        }

    def list_batches(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM batches ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "batch_id": row["batch_id"],
                "user_id": row["user_id"],
                "status": row["status"],
                "total": row["total"],
                "completed": row["completed"],
                "failed": row["failed"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "item_id": row["item_id"],
            "batch_id": row["batch_id"],
            "task_id": row["task_id"],
            "task_type": row["task_type"],
            "input_payload": json.loads(row["input_payload"]),
            "status": row["status"],
            "error": row["error"],
            "position": row["position"],
            "priority": int(row["priority"]) if "priority" in row.keys() else 0,
            "enqueued_at": row["enqueued_at"] if "enqueued_at" in row.keys() else "",
        }


_store: BatchStore | None = None


def get_batch_store() -> BatchStore:
    global _store
    if _store is None:
        _store = BatchStore()
    return _store
