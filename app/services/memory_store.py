from __future__ import annotations

import json
import math
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Optional

from app.config.settings import settings
from app.domain.memory import MemoryRecord
from app.services.db import postgres_connection, uses_postgres
from app.services.embedding_service import embed_text


class MemoryStore:
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
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    memory_type TEXT NOT NULL DEFAULT 'episode',
                    created_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column(conn, "memories", "embedding", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(
                conn,
                "memories",
                "memory_type",
                "TEXT NOT NULL DEFAULT 'episode'",
            )
            self._ensure_column(conn, "memories", "session_id", "TEXT NOT NULL DEFAULT ''")
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

    def write(self, record: MemoryRecord, *, memory_type: str = "episode") -> MemoryRecord:
        embedding = self._build_embedding(record)
        session_id = record.session_id or record.task_id
        params = (
            record.memory_id,
            record.task_id,
            record.user_id,
            record.task_type,
            record.summary,
            json.dumps(record.tags),
            json.dumps(record.payload),
            json.dumps(embedding),
            memory_type,
            record.created_at,
            session_id,
        )
        if uses_postgres():
            with postgres_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO memories
                    (memory_id, task_id, user_id, task_type, summary, tags, payload, embedding, memory_type, created_at, session_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (memory_id) DO UPDATE SET
                        task_id = EXCLUDED.task_id,
                        user_id = EXCLUDED.user_id,
                        task_type = EXCLUDED.task_type,
                        summary = EXCLUDED.summary,
                        tags = EXCLUDED.tags,
                        payload = EXCLUDED.payload,
                        embedding = EXCLUDED.embedding,
                        memory_type = EXCLUDED.memory_type,
                        created_at = EXCLUDED.created_at,
                        session_id = EXCLUDED.session_id
                    """,
                    params,
                )
            return record

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memories
                (memory_id, task_id, user_id, task_type, summary, tags, payload, embedding, memory_type, created_at, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params,
            )
            conn.commit()
        return record

    def delete_for_session(self, session_id: str) -> int:
        """Delete episode memories scoped to this session (and matching task_id)."""
        sid = str(session_id or "").strip()
        if not sid:
            return 0
        if uses_postgres():
            with postgres_connection() as conn:
                cur = conn.execute(
                    """
                    DELETE FROM memories
                    WHERE session_id = %s OR task_id = %s
                    """,
                    (sid, sid),
                )
                return int(getattr(cur, "rowcount", 0) or 0)

        with self._connect() as conn:
            cur = conn.execute(
                """
                DELETE FROM memories
                WHERE session_id = ? OR task_id = ?
                """,
                (sid, sid),
            )
            conn.commit()
            return int(cur.rowcount or 0)

    def search(
        self,
        query: str,
        limit: int = 5,
        user_id: Optional[str] = None,
        *,
        task_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        tokens = [t.lower() for t in query.split() if t.strip()]
        query_embedding: list[float] = []
        if query.strip():
            from app.services.query_embedding_context import get_scoped_embedding

            query_embedding = get_scoped_embedding(query) or embed_text(query)
        if uses_postgres():
            with postgres_connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM memories ORDER BY created_at DESC LIMIT 400"
                ).fetchall()
        else:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM memories ORDER BY created_at DESC LIMIT 400"
                ).fetchall()

        hits: list[dict[str, Any]] = []
        for row in rows:
            if user_id and row["user_id"] != user_id:
                continue
            payload = json.loads(row["payload"])
            tags_raw = json.loads(row["tags"])
            tags = [str(tag) for tag in tags_raw] if isinstance(tags_raw, list) else []
            haystack = f"{row['summary']} {' '.join(tags)} {json.dumps(payload, ensure_ascii=False)}".lower()
            keyword_score = self._keyword_score(tokens, haystack)
            semantic_score = self._semantic_score(query_embedding, row["embedding"])
            score = self._hybrid_score(keyword_score, semantic_score)
            score = self._apply_scope_boost(
                score,
                row=row,
                task_id=task_id,
                session_id=session_id,
            )
            if score <= 0:
                continue
            row_session = ""
            try:
                row_session = str(row["session_id"] or "")
            except (KeyError, IndexError):
                row_session = str(row["task_id"] or "")
            hits.append(
                {
                    "memory_id": row["memory_id"],
                    "task_id": row["task_id"],
                    "session_id": row_session,
                    "summary": row["summary"],
                    "score": score,
                    "keyword_score": keyword_score,
                    "semantic_score": semantic_score,
                    "memory_type": row["memory_type"],
                    "payload": payload,
                    "tags": tags,
                }
            )
        hits.sort(
            key=lambda item: (
                float(item["score"]),
                float(item["semantic_score"]),
                float(item["keyword_score"]),
            ),
            reverse=True,
        )
        return hits[:limit]

    def search_for_context(
        self,
        query: str,
        *,
        user_id: str,
        task_id: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 5,
        restrict_to_session: bool = False,
    ) -> list[dict[str, Any]]:
        """User-scoped search with task/session index boost."""
        sid = session_id or task_id
        hits = self.search(
            query,
            limit=limit if not restrict_to_session else max(limit, 20),
            user_id=user_id,
            task_id=task_id,
            session_id=sid,
        )
        if not restrict_to_session or not sid:
            return hits[:limit]
        scoped = [
            h
            for h in hits
            if str(h.get("session_id") or h.get("task_id") or "") == str(sid)
        ]
        return scoped[:limit]

    def _apply_scope_boost(
        self,
        score: float,
        *,
        row: Any,
        task_id: Optional[str],
        session_id: Optional[str],
    ) -> float:
        if score <= 0:
            return score
        boost_task = float(getattr(settings, "MEMORY_TASK_BOOST", 0.35))
        boost_session = float(getattr(settings, "MEMORY_SESSION_BOOST", 0.25))
        try:
            row_task = str(row["task_id"] or "")
            row_session = str(row["session_id"] or row_task)
        except (KeyError, IndexError):
            row_task = ""
            row_session = ""
        if task_id and row_task == task_id:
            score += boost_task
        if session_id and row_session == session_id:
            score += boost_session
        return score

    def write_structured_episode(self, state: dict[str, Any]) -> MemoryRecord:
        """Persist structured turn episode for user + task + session indexing."""
        from app.services.fact_layer import build_turn_facts

        turn_facts = state.get("turn_facts") or build_turn_facts(state)  # type: ignore[arg-type]
        reasoning = state.get("reasoning_result") or {}
        final = state.get("final_answer") or reasoning.get("summary") or ""

        payload = {
            "turn": turn_facts.get("turn"),
            "goal": turn_facts.get("goal"),
            "plan": turn_facts.get("plan"),
            "executed_actions": turn_facts.get("executed_actions"),
            "tools_executed": turn_facts.get("tools_executed"),
            "policy_result": state.get("policy_result"),
            "structured_output": state.get("structured_output"),
        }
        session_id = str(state.get("session_id") or state["task_id"])
        tags = [
            str(state.get("task_type", "qa")),
            "episode",
            f"turn:{turn_facts.get('turn', 0)}",
            f"session:{session_id[:8]}",
        ]

        summary_text = str(final)
        from app.services.memory_compress import compress_episode_summary

        compressed, was_compressed = compress_episode_summary(summary_text, payload=payload)
        if was_compressed:
            payload["raw_summary"] = summary_text[:12000]

        record = MemoryRecord(
            memory_id=str(uuid.uuid4()),
            task_id=state["task_id"],
            session_id=session_id,
            user_id=state.get("user_id", "anonymous"),
            task_type=state.get("task_type", "qa"),
            summary=compressed[:2000],
            tags=tags + (["compressed"] if was_compressed else []),
            payload=payload,
        )
        return self.write(record, memory_type="episode")

    def create_from_task(
        self,
        *,
        task_id: str,
        user_id: str,
        task_type: str,
        summary: str,
        tags: Optional[list[str]] = None,
        payload: Optional[dict[str, Any]] = None,
        memory_type: str = "episode",
        session_id: Optional[str] = None,
    ) -> MemoryRecord:
        sid = session_id or task_id
        record = MemoryRecord(
            memory_id=str(uuid.uuid4()),
            task_id=task_id,
            session_id=sid,
            user_id=user_id,
            task_type=task_type,
            summary=summary,
            tags=tags or [],
            payload=payload or {},
        )
        return self.write(record, memory_type=memory_type)

    def create_session_summary(
        self,
        *,
        task_id: str,
        user_id: str,
        task_type: str,
        summary: str,
        conversation_history: list[dict[str, Any]],
    ) -> MemoryRecord:
        compressed_history = conversation_history[-12:]
        return self.create_from_task(
            task_id=task_id,
            user_id=user_id,
            task_type=task_type,
            summary=summary,
            tags=[task_type, "session_summary", "compressed", f"session:{task_id[:8]}"],
            payload={
                "conversation_history": compressed_history,
                "history_turns": len(conversation_history),
                "session_id": task_id,
            },
            memory_type="session_summary",
        )

    def _build_embedding(self, record: MemoryRecord) -> list[float]:
        payload_preview = json.dumps(record.payload, ensure_ascii=False, sort_keys=True)[:4000]
        text = "\n".join(
            [
                record.task_type,
                record.summary,
                " ".join(record.tags),
                payload_preview,
            ]
        ).strip()
        return embed_text(text) if text else []

    def _keyword_score(self, tokens: list[str], haystack: str) -> float:
        if not tokens:
            return 0.0
        matches = sum(1 for token in tokens if token in haystack)
        return matches / max(len(tokens), 1)

    def _semantic_score(self, query_embedding: list[float], raw_embedding: Any) -> float:
        if not query_embedding:
            return 0.0
        try:
            stored = json.loads(raw_embedding) if isinstance(raw_embedding, str) else raw_embedding
        except json.JSONDecodeError:
            return 0.0
        if not isinstance(stored, list) or not stored:
            return 0.0
        values = [float(item) for item in stored]
        length = min(len(query_embedding), len(values))
        if length == 0:
            return 0.0
        query_vec = query_embedding[:length]
        stored_vec = values[:length]
        return max(0.0, self._cosine_similarity(query_vec, stored_vec))

    def _hybrid_score(self, keyword_score: float, semantic_score: float) -> float:
        if keyword_score <= 0 and semantic_score <= 0:
            return 0.0
        if keyword_score <= 0:
            return semantic_score * 0.85
        if semantic_score <= 0:
            return keyword_score * 0.75
        return keyword_score * 0.4 + semantic_score * 0.6

    def _cosine_similarity(self, left: list[float], right: list[float]) -> float:
        numerator = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(a * a for a in left)) or 1.0
        right_norm = math.sqrt(sum(b * b for b in right)) or 1.0
        return numerator / (left_norm * right_norm)


_store: MemoryStore | None = None
_stores: dict[str, MemoryStore] = {}


def get_memory_store() -> MemoryStore:
    from app.services.tenant_storage import current_sqlite_path, tenant_cache_key

    key = tenant_cache_key()
    if key == "default":
        global _store
        if _store is None:
            _store = MemoryStore()
        return _store
    if key not in _stores:
        _stores[key] = MemoryStore(db_path=current_sqlite_path())
    return _stores[key]
