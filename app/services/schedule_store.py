from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config.settings import settings


class ScheduleStore:
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
                CREATE TABLE IF NOT EXISTS schedules (
                    schedule_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    cron_expression TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    input_payload TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    last_run_at TEXT,
                    next_run_hint TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column(conn, "schedules", "priority", "INTEGER NOT NULL DEFAULT 0")
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

    def create(
        self,
        *,
        name: str,
        cron_expression: str,
        user_id: str,
        task_type: str,
        input_payload: dict[str, Any],
        enabled: bool = True,
        priority: int = 0,
    ) -> dict[str, Any]:
        schedule_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO schedules
                (schedule_id, name, cron_expression, user_id, task_type, input_payload,
                 enabled, last_run_at, next_run_hint, created_at, updated_at, priority)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
                """,
                (
                    schedule_id,
                    name,
                    cron_expression,
                    user_id,
                    task_type,
                    json.dumps(input_payload),
                    1 if enabled else 0,
                    now,
                    now,
                    int(priority),
                ),
            )
            conn.commit()
        return self.get(schedule_id) or {}

    def get(self, schedule_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM schedules WHERE schedule_id = ?",
                (schedule_id,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_all(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM schedules ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def list_enabled(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM schedules WHERE enabled = 1 ORDER BY created_at ASC"
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def set_enabled(self, schedule_id: str, enabled: bool) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE schedules SET enabled = ?, updated_at = ? WHERE schedule_id = ?",
                (1 if enabled else 0, now, schedule_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def delete(self, schedule_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM schedules WHERE schedule_id = ?", (schedule_id,))
            conn.commit()
            return cursor.rowcount > 0

    def set_next_run_hint(self, schedule_id: str, next_run_hint: Optional[str]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE schedules SET next_run_hint = ?, updated_at = ? WHERE schedule_id = ?",
                (next_run_hint, now, schedule_id),
            )
            conn.commit()

    def mark_run(self, schedule_id: str, next_run_hint: Optional[str] = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE schedules
                SET last_run_at = ?, next_run_hint = ?, updated_at = ?
                WHERE schedule_id = ?
                """,
                (now, next_run_hint, now, schedule_id),
            )
            conn.commit()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schedule_id": row["schedule_id"],
            "name": row["name"],
            "cron_expression": row["cron_expression"],
            "user_id": row["user_id"],
            "task_type": row["task_type"],
            "input_payload": json.loads(row["input_payload"]),
            "enabled": bool(row["enabled"]),
            "last_run_at": row["last_run_at"],
            "next_run_hint": row["next_run_hint"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "priority": int(row["priority"]) if "priority" in row.keys() else 0,
        }


_store: ScheduleStore | None = None


def get_schedule_store() -> ScheduleStore:
    global _store
    if _store is None:
        _store = ScheduleStore()
    return _store
