"""Persist full LLM request/response pairs per task for monitoring."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config.settings import settings
from app.services.db import postgres_connection, uses_postgres


def _truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0 or len(text) <= max_chars:
        return text, False
    return text[:max_chars] + "\n…[truncated]", True


def build_llm_request_payload(system_prompt: str, user_content: str) -> dict[str, Any]:
    """Canonical request shape for monitoring APIs."""
    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
    }


class LlmInteractionStore:
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
                CREATE TABLE IF NOT EXISTS llm_interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    response_text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'llm_client',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_llm_interactions_task_id
                ON llm_interactions (task_id, id)
                """
            )
            conn.commit()

    def record(
        self,
        *,
        task_id: str,
        session_id: str,
        purpose: str,
        system_prompt: str,
        user_content: str,
        response_text: str,
        status: str = "ok",
        source: str = "llm_client",
    ) -> None:
        if not getattr(settings, "LLM_INTERACTION_LOG_ENABLED", True):
            return
        if not task_id:
            return
        max_chars = int(getattr(settings, "LLM_INTERACTION_LOG_MAX_CHARS", 200_000))
        sys_text, sys_trunc = _truncate_text(system_prompt or "", max_chars)
        user_text, user_trunc = _truncate_text(user_content or "", max_chars)
        resp_text, resp_trunc = _truncate_text(response_text or "", max_chars)
        request = build_llm_request_payload(sys_text, user_text)
        if sys_trunc or user_trunc or resp_trunc:
            request["truncated"] = True
        now = datetime.now(timezone.utc).isoformat()
        request_json = json.dumps(request, ensure_ascii=False)
        sid = session_id or task_id

        if uses_postgres():
            with postgres_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO llm_interactions
                    (task_id, session_id, purpose, request_json, response_text, status, source, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (task_id, sid, purpose, request_json, resp_text, status, source, now),
                )
            self._enforce_retention(task_id)
            return

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO llm_interactions
                (task_id, session_id, purpose, request_json, response_text, status, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (task_id, sid, purpose, request_json, resp_text, status, source, now),
            )
            conn.commit()
        self._enforce_retention(task_id)

    def retention_limits(self) -> dict[str, int]:
        """Configured caps exposed for monitoring API."""
        max_rows, max_bytes = self._retention_limits()
        return {"max_per_task": max_rows, "max_bytes_per_task": max_bytes}

    def _retention_limits(self) -> tuple[int, int]:
        """(max_rows, max_bytes). Zero disables that cap."""
        max_rows = int(getattr(settings, "LLM_INTERACTION_LOG_MAX_PER_TASK", 120) or 0)
        max_bytes = int(getattr(settings, "LLM_INTERACTION_LOG_MAX_BYTES_PER_TASK", 0) or 0)
        return max_rows, max_bytes

    def _list_row_byte_sizes(self, task_id: str) -> list[tuple[int, int]]:
        if uses_postgres():
            with postgres_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT id,
                           LENGTH(request_json) + LENGTH(response_text) AS byte_size
                    FROM llm_interactions
                    WHERE task_id = %s
                    ORDER BY id ASC
                    """,
                    (task_id,),
                ).fetchall()
        else:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id,
                           LENGTH(request_json) + LENGTH(response_text) AS byte_size
                    FROM llm_interactions
                    WHERE task_id = ?
                    ORDER BY id ASC
                    """,
                    (task_id,),
                ).fetchall()
        return [(int(r["id"]), int(r["byte_size"] or 0)) for r in rows]

    def bytes_for_task(self, task_id: str) -> int:
        return sum(size for _, size in self._list_row_byte_sizes(task_id))

    def _delete_ids_for_task(self, task_id: str, delete_ids: list[int]) -> None:
        if not delete_ids:
            return
        if uses_postgres():
            with postgres_connection() as conn:
                conn.execute(
                    "DELETE FROM llm_interactions WHERE task_id = %s AND id = ANY(%s)",
                    (task_id, delete_ids),
                )
            return
        placeholders = ",".join("?" for _ in delete_ids)
        with self._connect() as conn:
            conn.execute(
                f"""
                DELETE FROM llm_interactions
                WHERE task_id = ? AND id IN ({placeholders})
                """,
                (task_id, *delete_ids),
            )
            conn.commit()

    def _enforce_retention(self, task_id: str) -> None:
        """
        Drop oldest rows while count > max_rows OR total stored bytes > max_bytes.
        """
        max_rows, max_bytes = self._retention_limits()
        if max_rows <= 0 and max_bytes <= 0:
            return

        rows = self._list_row_byte_sizes(task_id)
        if not rows:
            return

        total_count = len(rows)
        total_bytes = sum(size for _, size in rows)
        delete_ids: list[int] = []
        idx = 0
        while idx < len(rows):
            over_rows = max_rows > 0 and total_count > max_rows
            over_bytes = max_bytes > 0 and total_bytes > max_bytes
            if not over_rows and not over_bytes:
                break
            row_id, row_bytes = rows[idx]
            delete_ids.append(row_id)
            total_count -= 1
            total_bytes -= row_bytes
            idx += 1

        self._delete_ids_for_task(task_id, delete_ids)

    def _row_to_interaction(self, row: Any, *, index: int) -> dict[str, Any]:
        request = json.loads(row["request_json"])
        return {
            "index": index,
            "id": int(row["id"]),
            "task_id": row["task_id"],
            "session_id": row["session_id"],
            "purpose": row["purpose"],
            "source": row["source"],
            "status": row["status"],
            "created_at": row["created_at"],
            "request": request,
            "response": row["response_text"],
        }

    def _row_to_summary(self, row: Any, *, index: int) -> dict[str, Any]:
        request = json.loads(row["request_json"])
        messages = request.get("messages") or []
        request_chars = sum(len(str(m.get("content") or "")) for m in messages)
        return {
            "index": index,
            "id": int(row["id"]),
            "purpose": row["purpose"],
            "source": row["source"],
            "status": row["status"],
            "created_at": row["created_at"],
            "request_chars": request_chars,
            "response_chars": len(row["response_text"] or ""),
            "truncated": bool(request.get("truncated")),
        }

    def count_for_task(self, task_id: str) -> int:
        if uses_postgres():
            with postgres_connection() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM llm_interactions WHERE task_id = %s",
                    (task_id,),
                ).fetchone()
            return int(row["c"] if row else 0)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM llm_interactions WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return int(row["c"] if row else 0)

    def list_summaries_for_task(self, task_id: str) -> list[dict[str, Any]]:
        if uses_postgres():
            with postgres_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT id, purpose, request_json, response_text, status, source, created_at
                    FROM llm_interactions
                    WHERE task_id = %s
                    ORDER BY id ASC
                    """,
                    (task_id,),
                ).fetchall()
        else:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, purpose, request_json, response_text, status, source, created_at
                    FROM llm_interactions
                    WHERE task_id = ?
                    ORDER BY id ASC
                    """,
                    (task_id,),
                ).fetchall()
        return [self._row_to_summary(row, index=i + 1) for i, row in enumerate(rows)]

    def get_by_index(self, task_id: str, index: int) -> Optional[dict[str, Any]]:
        if index < 1:
            return None
        offset = index - 1
        if uses_postgres():
            with postgres_connection() as conn:
                row = conn.execute(
                    """
                    SELECT id, task_id, session_id, purpose, request_json, response_text,
                           status, source, created_at
                    FROM llm_interactions
                    WHERE task_id = %s
                    ORDER BY id ASC
                    OFFSET %s LIMIT 1
                    """,
                    (task_id, offset),
                ).fetchone()
        else:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT id, task_id, session_id, purpose, request_json, response_text,
                           status, source, created_at
                    FROM llm_interactions
                    WHERE task_id = ?
                    ORDER BY id ASC
                    LIMIT 1 OFFSET ?
                    """,
                    (task_id, offset),
                ).fetchone()
        if not row:
            return None
        return self._row_to_interaction(row, index=index)

    def list_for_task(self, task_id: str) -> list[dict[str, Any]]:
        if uses_postgres():
            with postgres_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT id, task_id, session_id, purpose, request_json, response_text,
                           status, source, created_at
                    FROM llm_interactions
                    WHERE task_id = %s
                    ORDER BY id ASC
                    """,
                    (task_id,),
                ).fetchall()
        else:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, task_id, session_id, purpose, request_json, response_text,
                           status, source, created_at
                    FROM llm_interactions
                    WHERE task_id = ?
                    ORDER BY id ASC
                    """,
                    (task_id,),
                ).fetchall()

        return [self._row_to_interaction(row, index=i + 1) for i, row in enumerate(rows)]

    def delete_for_task(self, task_id: str) -> int:
        if uses_postgres():
            with postgres_connection() as conn:
                cur = conn.execute(
                    "DELETE FROM llm_interactions WHERE task_id = %s",
                    (task_id,),
                )
                return int(getattr(cur, "rowcount", 0) or 0)

        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM llm_interactions WHERE task_id = ?",
                (task_id,),
            )
            conn.commit()
            return int(cur.rowcount or 0)


_store: LlmInteractionStore | None = None
_stores: dict[str, LlmInteractionStore] = {}


def get_llm_interaction_store() -> LlmInteractionStore:
    from app.services.tenant_storage import current_sqlite_path, tenant_cache_key

    key = tenant_cache_key()
    if key == "default":
        global _store
        if _store is None:
            _store = LlmInteractionStore()
        return _store
    if key not in _stores:
        _stores[key] = LlmInteractionStore(db_path=current_sqlite_path())
    return _stores[key]


def record_llm_interaction(
    *,
    trace_state: Any | None,
    purpose: str,
    system_prompt: str,
    user_content: str,
    response_text: str,
    status: str = "ok",
    source: str = "llm_client",
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    """Best-effort append; never raises to callers."""
    try:
        tid = task_id
        sid = session_id
        if isinstance(trace_state, dict):
            tid = tid or str(trace_state.get("task_id") or "")
            sid = sid or str(trace_state.get("session_id") or trace_state.get("thread_id") or tid or "")
        if not tid:
            return
        get_llm_interaction_store().record(
            task_id=tid,
            session_id=sid or tid,
            purpose=purpose,
            system_prompt=system_prompt,
            user_content=user_content,
            response_text=response_text,
            status=status,
            source=source,
        )
    except Exception:
        pass
