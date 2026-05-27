from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config.settings import settings
from app.runtime.state import AgentState, TaskStatus, create_initial_state, merge_state
from app.services.db import postgres_connection, uses_postgres


class DeadLetterStore:
    """Dead letter queue for tasks that exceeded retry limits (§22.4)."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or settings.SQLITE_PATH
        if not uses_postgres():
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._init_sqlite()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_sqlite(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dead_letter_queue (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    errors TEXT NOT NULL,
                    retry_count INTEGER NOT NULL,
                    enqueued_at TEXT NOT NULL,
                    resolved_at TEXT
                )
                """
            )
            conn.commit()

    def enqueue(self, state: AgentState) -> None:
        now = datetime.now(timezone.utc).isoformat()
        params = (
            state["task_id"],
            str(state.get("status", TaskStatus.DEAD_LETTER.value)),
            json.dumps(dict(state)),
            json.dumps(state.get("errors", [])),
            int(state.get("retry_count", 0)),
            now,
        )
        if uses_postgres():
            with postgres_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO dead_letter_queue
                    (task_id, status, state_json, errors, retry_count, enqueued_at, resolved_at)
                    VALUES (%s, %s, %s, %s, %s, %s, NULL)
                    ON CONFLICT (task_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        state_json = EXCLUDED.state_json,
                        errors = EXCLUDED.errors,
                        retry_count = EXCLUDED.retry_count,
                        enqueued_at = EXCLUDED.enqueued_at,
                        resolved_at = NULL
                    """,
                    params,
                )
            return

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO dead_letter_queue
                (task_id, status, state_json, errors, retry_count, enqueued_at, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                params,
            )
            conn.commit()

    def list_entries(self, limit: int = 50) -> list[dict[str, Any]]:
        if uses_postgres():
            with postgres_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT task_id, status, errors, retry_count, enqueued_at, resolved_at
                    FROM dead_letter_queue
                    ORDER BY enqueued_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                ).fetchall()
        else:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT task_id, status, errors, retry_count, enqueued_at, resolved_at
                    FROM dead_letter_queue
                    ORDER BY enqueued_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [
            {
                "task_id": row["task_id"],
                "status": row["status"],
                "errors": json.loads(row["errors"]),
                "retry_count": row["retry_count"],
                "enqueued_at": row["enqueued_at"],
                "resolved_at": row["resolved_at"],
            }
            for row in rows
        ]

    def get(self, task_id: str) -> Optional[dict[str, Any]]:
        if uses_postgres():
            with postgres_connection() as conn:
                row = conn.execute(
                    "SELECT * FROM dead_letter_queue WHERE task_id = %s",
                    (task_id,),
                ).fetchone()
        else:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM dead_letter_queue WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
        if not row:
            return None
        return {
            "task_id": row["task_id"],
            "status": row["status"],
            "state": json.loads(row["state_json"]),
            "errors": json.loads(row["errors"]),
            "retry_count": row["retry_count"],
            "enqueued_at": row["enqueued_at"],
            "resolved_at": row["resolved_at"],
        }

    def requeue(self, task_id: str) -> Optional[AgentState]:
        entry = self.get(task_id)
        if not entry or entry.get("resolved_at"):
            return None
        now = datetime.now(timezone.utc).isoformat()
        if uses_postgres():
            with postgres_connection() as conn:
                conn.execute(
                    "UPDATE dead_letter_queue SET resolved_at = %s WHERE task_id = %s",
                    (now, task_id),
                )
        else:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE dead_letter_queue SET resolved_at = ? WHERE task_id = ?",
                    (now, task_id),
                )
                conn.commit()
        raw = entry["state"]
        reset = create_initial_state(
            task_id=raw["task_id"],
            session_id=raw.get("session_id"),
            user_id=raw.get("user_id", "anonymous"),
            task_type=raw.get("task_type", "qa"),
            input_payload=raw.get("input_payload", {}),
        )
        return merge_state(
            reset,
            execution_mode=raw.get("execution_mode", "single"),
            errors=[],
            retry_count=0,
            status=TaskStatus.NEW.value,
            current_node="api",
        )


_store: DeadLetterStore | None = None
_stores: dict[str, DeadLetterStore] = {}


def get_dead_letter_store() -> DeadLetterStore:
    from app.services.tenant_storage import current_sqlite_path, tenant_cache_key

    key = tenant_cache_key()
    if key == "default":
        global _store
        if _store is None:
            _store = DeadLetterStore()
        return _store
    if key not in _stores:
        _stores[key] = DeadLetterStore(db_path=current_sqlite_path())
    return _stores[key]
