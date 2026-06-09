"""Chat message persistence store (Phase 2+3)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config.settings import settings
from app.services.db import postgres_connection, uses_postgres

MESSAGE_STATUS_STREAMING = "streaming"
MESSAGE_STATUS_COMPLETED = "completed"
MESSAGE_STATUS_INTERRUPTED = "interrupted"
MESSAGE_STATUS_FAILED = "failed"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_message_id() -> str:
    return str(uuid.uuid4())


class ChatMessageStore:
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
                CREATE TABLE IF NOT EXISTS chat_messages (
                    message_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    session_turn INTEGER NOT NULL DEFAULT 0,
                    role TEXT NOT NULL,
                    status TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    structured_blocks_json TEXT NOT NULL DEFAULT '[]',
                    client_message_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_messages_task_turn
                ON chat_messages (task_id, session_turn, created_at)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_message_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    delta TEXT NOT NULL DEFAULT '',
                    meta_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE (task_id, seq)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_message_events_task_seq
                ON chat_message_events (task_id, seq)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_message_events_message
                ON chat_message_events (message_id, seq)
                """
            )
            conn.commit()

    def create_message(
        self,
        *,
        task_id: str,
        session_id: str,
        session_turn: int,
        role: str,
        status: str,
        content: str = "",
        structured_blocks: Optional[list[dict[str, Any]]] = None,
        client_message_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> dict[str, Any]:
        mid = message_id or _new_message_id()
        now = _now_iso()
        blocks = structured_blocks or []
        row = {
            "message_id": mid,
            "task_id": task_id,
            "session_id": session_id,
            "session_turn": int(session_turn),
            "role": role,
            "status": status,
            "content": content,
            "structured_blocks": blocks,
            "client_message_id": client_message_id,
            "created_at": now,
            "updated_at": now,
        }
        blocks_json = json.dumps(blocks, ensure_ascii=False)
        if uses_postgres():
            with postgres_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO chat_messages (
                        message_id, task_id, session_id, session_turn, role, status,
                        content, structured_blocks_json, client_message_id, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        mid,
                        task_id,
                        session_id,
                        int(session_turn),
                        role,
                        status,
                        content,
                        blocks_json,
                        client_message_id,
                        now,
                        now,
                    ),
                )
            return row

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_messages (
                    message_id, task_id, session_id, session_turn, role, status,
                    content, structured_blocks_json, client_message_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mid,
                    task_id,
                    session_id,
                    int(session_turn),
                    role,
                    status,
                    content,
                    blocks_json,
                    client_message_id,
                    now,
                    now,
                ),
            )
            conn.commit()
        return row

    def update_message(
        self,
        message_id: str,
        *,
        status: Optional[str] = None,
        content: Optional[str] = None,
        structured_blocks: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        now = _now_iso()
        if uses_postgres():
            sets = ["updated_at = %s"]
            params: list[Any] = [now]
            if status is not None:
                sets.append("status = %s")
                params.append(status)
            if content is not None:
                sets.append("content = %s")
                params.append(content)
            if structured_blocks is not None:
                sets.append("structured_blocks_json = %s")
                params.append(json.dumps(structured_blocks, ensure_ascii=False))
            params.append(message_id)
            with postgres_connection() as conn:
                conn.execute(
                    f"UPDATE chat_messages SET {', '.join(sets)} WHERE message_id = %s",
                    tuple(params),
                )
            return

        sets = ["updated_at = ?"]
        params = [now]
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if content is not None:
            sets.append("content = ?")
            params.append(content)
        if structured_blocks is not None:
            sets.append("structured_blocks_json = ?")
            params.append(json.dumps(structured_blocks, ensure_ascii=False))
        params.append(message_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE chat_messages SET {', '.join(sets)} WHERE message_id = ?",
                tuple(params),
            )
            conn.commit()

    def list_messages(self, task_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        if uses_postgres():
            with postgres_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM chat_messages
                    WHERE task_id = %s
                    ORDER BY session_turn ASC, created_at ASC
                    LIMIT %s
                    """,
                    (task_id, int(limit)),
                ).fetchall()
            return [self._row_to_message(r) for r in rows]

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM chat_messages
                WHERE task_id = ?
                ORDER BY session_turn ASC, created_at ASC
                LIMIT ?
                """,
                (task_id, int(limit)),
            ).fetchall()
        return [self._row_to_message(r) for r in rows]

    def next_event_seq(self, task_id: str) -> int:
        if uses_postgres():
            with postgres_connection() as conn:
                row = conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM chat_message_events WHERE task_id = %s",
                    (task_id,),
                ).fetchone()
            return int(row["max_seq"]) + 1 if row else 1
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM chat_message_events WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return int(row["max_seq"]) + 1

    def append_event(
        self,
        *,
        message_id: str,
        task_id: str,
        seq: int,
        event_type: str,
        delta: str = "",
        meta: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        now = _now_iso()
        meta_json = json.dumps(meta or {}, ensure_ascii=False)
        event = {
            "message_id": message_id,
            "task_id": task_id,
            "seq": int(seq),
            "event_type": event_type,
            "delta": delta,
            "meta": meta or {},
            "created_at": now,
        }
        if uses_postgres():
            with postgres_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO chat_message_events (
                        message_id, task_id, seq, event_type, delta, meta_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (message_id, task_id, int(seq), event_type, delta, meta_json, now),
                )
            return event

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_message_events (
                    message_id, task_id, seq, event_type, delta, meta_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, task_id, int(seq), event_type, delta, meta_json, now),
            )
            conn.commit()
        return event

    def list_events(
        self,
        task_id: str,
        *,
        after_seq: int = 0,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        if uses_postgres():
            with postgres_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM chat_message_events
                    WHERE task_id = %s AND seq > %s
                    ORDER BY seq ASC
                    LIMIT %s
                    """,
                    (task_id, int(after_seq), int(limit)),
                ).fetchall()
            return [self._row_to_event(r) for r in rows]

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM chat_message_events
                WHERE task_id = ? AND seq > ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (task_id, int(after_seq), int(limit)),
            ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def delete_for_task(self, task_id: str) -> dict[str, int]:
        if uses_postgres():
            with postgres_connection() as conn:
                ev = conn.execute(
                    "DELETE FROM chat_message_events WHERE task_id = %s",
                    (task_id,),
                )
                msg = conn.execute(
                    "DELETE FROM chat_messages WHERE task_id = %s",
                    (task_id,),
                )
            return {
                "events_removed": int(getattr(ev, "rowcount", 0) or 0),
                "messages_removed": int(getattr(msg, "rowcount", 0) or 0),
            }

        with self._connect() as conn:
            ev = conn.execute(
                "DELETE FROM chat_message_events WHERE task_id = ?",
                (task_id,),
            )
            msg = conn.execute(
                "DELETE FROM chat_messages WHERE task_id = ?",
                (task_id,),
            )
            conn.commit()
        return {
            "events_removed": int(ev.rowcount or 0),
            "messages_removed": int(msg.rowcount or 0),
        }

    @staticmethod
    def _row_to_message(row: Any) -> dict[str, Any]:
        if not hasattr(row, "get"):
            row = dict(row)
        blocks_raw = row["structured_blocks_json"]
        if isinstance(blocks_raw, str):
            try:
                blocks = json.loads(blocks_raw)
            except json.JSONDecodeError:
                blocks = []
        else:
            blocks = blocks_raw or []
        return {
            "message_id": str(row["message_id"]),
            "task_id": str(row["task_id"]),
            "session_id": str(row["session_id"]),
            "session_turn": int(row["session_turn"]),
            "role": str(row["role"]),
            "status": str(row["status"]),
            "content": str(row["content"] or ""),
            "structured_blocks": blocks if isinstance(blocks, list) else [],
            "client_message_id": row.get("client_message_id"),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    @staticmethod
    def _row_to_event(row: Any) -> dict[str, Any]:
        if not hasattr(row, "get"):
            row = dict(row)
        meta_raw = row["meta_json"]
        if isinstance(meta_raw, str):
            try:
                meta = json.loads(meta_raw)
            except json.JSONDecodeError:
                meta = {}
        else:
            meta = meta_raw or {}
        return {
            "message_id": str(row["message_id"]),
            "task_id": str(row["task_id"]),
            "seq": int(row["seq"]),
            "event_type": str(row["event_type"]),
            "delta": str(row["delta"] or ""),
            "meta": meta if isinstance(meta, dict) else {},
            "created_at": str(row["created_at"]),
        }


_store: ChatMessageStore | None = None
_stores: dict[str, ChatMessageStore] = {}


def get_chat_message_store() -> ChatMessageStore:
    from app.services.tenant_storage import current_sqlite_path, tenant_cache_key

    key = tenant_cache_key()
    if key == "default":
        global _store
        if _store is None:
            _store = ChatMessageStore()
        return _store
    if key not in _stores:
        _stores[key] = ChatMessageStore(db_path=current_sqlite_path())
    return _stores[key]
