from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config.settings import settings
from app.services.db import postgres_connection, uses_postgres


class AuditStore:
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
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def append_events(self, task_id: str, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        now = datetime.now(timezone.utc).isoformat()
        if uses_postgres():
            with postgres_connection() as conn:
                for event in events:
                    conn.execute(
                        "INSERT INTO audit_events (task_id, event_json, created_at) VALUES (%s, %s, %s)",
                        (task_id, json.dumps(event), now),
                    )
            return

        with self._connect() as conn:
            for event in events:
                conn.execute(
                    "INSERT INTO audit_events (task_id, event_json, created_at) VALUES (?, ?, ?)",
                    (task_id, json.dumps(event), now),
                )
            conn.commit()

    def delete_events_for_task(self, task_id: str) -> int:
        """Remove all audit rows for a task/session."""
        if uses_postgres():
            with postgres_connection() as conn:
                cur = conn.execute(
                    "DELETE FROM audit_events WHERE task_id = %s",
                    (task_id,),
                )
                return int(getattr(cur, "rowcount", 0) or 0)

        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM audit_events WHERE task_id = ?",
                (task_id,),
            )
            conn.commit()
            return int(cur.rowcount or 0)

    def get_chain(self, task_id: str) -> list[dict[str, Any]]:
        if uses_postgres():
            with postgres_connection() as conn:
                rows = conn.execute(
                    "SELECT event_json FROM audit_events WHERE task_id = %s ORDER BY id ASC",
                    (task_id,),
                ).fetchall()
            return [json.loads(row["event_json"]) for row in rows]

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT event_json FROM audit_events WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
        return [json.loads(row["event_json"]) for row in rows]


_store: AuditStore | None = None
_stores: dict[str, AuditStore] = {}


def get_audit_store() -> AuditStore:
    from app.services.tenant_storage import current_sqlite_path, tenant_cache_key

    key = tenant_cache_key()
    if key == "default":
        global _store
        if _store is None:
            _store = AuditStore()
        return _store
    if key not in _stores:
        _stores[key] = AuditStore(db_path=current_sqlite_path())
    return _stores[key]
