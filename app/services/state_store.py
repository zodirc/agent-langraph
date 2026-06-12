"""任务状态持久化
graph_runner 每轮 save；API GET /tasks/{id}/status 读取；合并时保留 steer 等易失字段。

Persist AgentState snapshots (SQLite or PostgreSQL per tenant).
Volatile keys (_VOLATILE_*) preserved across partial LangGraph node snapshots."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from app.config.settings import settings
from app.domain.task import TaskRecord
from app.runtime.state import AgentState, TaskStatus, ensure_agent_state

# Top-level fields set by steer / pause but often absent from LangGraph node snapshots.
_VOLATILE_STATE_KEYS = (
    "pending_user_message",
    "steer_applied_at",
    "steer_intent_pending_confirm",
    "steer_intent_confirmation",
    "steer_outcome_pending_confirm",
    "steer_outcome_confirmation",
)

# Nested under input_payload — same loss pattern during mission_act / writing_node saves.
_PAYLOAD_VOLATILE_KEYS = (
    "execution_grant",
    "last_execution_grant",
    "require_planning_after_steer",
    "require_planning_after_contract_invalidation",
    "turn_contract_invalidation",
    "steer_planning_done",
    "steer_watch_outcome",
    "steer_intent_pending_confirm",
    "steer_intent_confirmed",
    "steer_intent_confirmation",
    "steer_outcome_pending_confirm",
    "steer_outcome_confirmation",
    "steer_review_outline",
    "mission_intervention",
    "fsm_state",
    "session_mode",
    "last_applied_message_id",
    "turn_step_executed",
    "steer_planning_complete_pending",
)

# Cleared on each new steer batch — must not be restored from DB after pop/absent in snapshot.
_STEER_BATCH_GATE_KEYS = frozenset(
    {
        "steer_intent_pending_confirm",
        "steer_intent_confirmation",
        "steer_intent_confirmed",
        "steer_outcome_pending_confirm",
        "steer_outcome_confirmation",
        "steer_outcome_confirmed",
    }
)
from app.services.db import (
    postgres_connection,
    postgres_read_connection,
    postgres_uses_read_replica,
    run_with_retry,
    uses_postgres,
)
from app.runtime.agent_state_model import state_to_model

def merge_input_payload_for_gates(
    state: AgentState,
    stored: Optional[AgentState] = None,
) -> dict[str, Any]:
    """
    Merge in-memory and persisted payloads for confirmation gates.

    In-graph snapshots often omit steer gates; persisted rows may still hold them
    unless a new steer_applied_at batch cleared them explicitly.
    """
    cur = dict(state.get("input_payload") or {})
    prev = dict((stored or state).get("input_payload") or {})
    if cur.get("steer_applied_at") and cur.get("steer_applied_at") != prev.get("steer_applied_at"):
        for key in _STEER_BATCH_GATE_KEYS:
            prev.pop(key, None)
    merged = {**prev, **cur}
    # Gate flags: in-graph snapshots often carry explicit False while DB still has
    # pending True until save; OR-merge so pause/autonomous UI matches resume_mission.
    for key in ("steer_outcome_pending_confirm", "steer_intent_pending_confirm"):
        if cur.get(key) is True or prev.get(key) is True:
            merged[key] = True
        elif cur.get(key) is False:
            merged[key] = False
    return merged


class StateStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or settings.SQLITE_PATH
        if not uses_postgres():
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._init_sqlite()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError:
            conn.execute("PRAGMA journal_mode=DELETE")
        return conn

    def _init_sqlite(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_states (
                    task_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_node TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def save(self, state: AgentState) -> AgentState:
        return run_with_retry("state_store.save", lambda: self._save(state))

    def _preserve_volatile_fields(self, state: AgentState) -> AgentState:
        """Keep steer/planning-gate fields when in-graph snapshots omit them (mission_act, writing)."""
        existing = self.load(state["task_id"], read_only=True)
        if not existing:
            return state
        merged = dict(state)
        for key in _VOLATILE_STATE_KEYS:
            if existing.get(key) is not None and key not in merged:
                merged[key] = existing[key]
        existing_payload = existing.get("input_payload") or {}
        if isinstance(existing_payload, dict):
            incoming = merged.get("input_payload") or {}
            if not isinstance(incoming, dict):
                incoming = {}
            payload_merged = dict(incoming)
            incoming_steer_at = merged.get("steer_applied_at") or payload_merged.get(
                "steer_applied_at"
            )
            existing_steer_at = existing.get("steer_applied_at") or existing_payload.get(
                "steer_applied_at"
            )
            new_steer_batch = bool(
                incoming_steer_at
                and incoming_steer_at != existing_steer_at
            )
            for key in _PAYLOAD_VOLATILE_KEYS:
                if key not in existing_payload or key in payload_merged:
                    continue
                if new_steer_batch and key in _STEER_BATCH_GATE_KEYS:
                    continue
                payload_merged[key] = existing_payload[key]
            merged["input_payload"] = payload_merged
        existing_progress = existing.get("progress")
        incoming_progress = merged.get("progress")
        if isinstance(existing_progress, dict) and isinstance(incoming_progress, dict):
            merged_progress = {**existing_progress, **incoming_progress}
            ex_ws = existing_progress.get("writing_state")
            in_ws = incoming_progress.get("writing_state")
            if isinstance(ex_ws, dict):
                if not isinstance(in_ws, dict):
                    merged_progress["writing_state"] = ex_ws
                elif ex_ws.get("phases_done") and not in_ws.get("phases_done"):
                    merged_progress["writing_state"] = {**ex_ws, **in_ws, "phases_done": ex_ws["phases_done"]}
            if existing_progress.get("work_plan") and not incoming_progress.get("work_plan"):
                merged_progress["work_plan"] = existing_progress["work_plan"]
            merged["progress"] = merged_progress
        return ensure_agent_state(merged)

    def _save(self, state: AgentState) -> AgentState:
        from app.services.task_tombstone import is_task_tombstoned

        if is_task_tombstoned(str(state.get("task_id") or "")):
            return state
        state = self._preserve_volatile_fields(state)
        from app.services.invariant_guard import validate
        from app.runtime.agent_state_model import validate_session_snapshot

        state = validate(state)
        validate_session_snapshot(state)
        now = datetime.now(timezone.utc).isoformat()
        record = TaskRecord(
            task_id=state["task_id"],
            session_id=state["session_id"],
            user_id=state["user_id"],
            task_type=state["task_type"],
            status=str(state["status"]),
            current_node=state["current_node"],
            input_payload=state["input_payload"],
            final_answer=state.get("final_answer"),
            structured_output=state.get("structured_output"),
            artifacts=state.get("artifacts"),
        )
        state_json = json.dumps(state_to_model(state).model_dump())

        if uses_postgres():
            with postgres_connection() as conn:
                row = conn.execute(
                    "SELECT created_at FROM task_states WHERE task_id = %s",
                    (state["task_id"],),
                ).fetchone()
                created_at = row["created_at"] if row else now
                conn.execute(
                    """
                    INSERT INTO task_states
                    (task_id, session_id, user_id, task_type, status, current_node,
                     state_json, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (task_id) DO UPDATE SET
                        session_id = EXCLUDED.session_id,
                        user_id = EXCLUDED.user_id,
                        task_type = EXCLUDED.task_type,
                        status = EXCLUDED.status,
                        current_node = EXCLUDED.current_node,
                        state_json = EXCLUDED.state_json,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        record.task_id,
                        record.session_id,
                        record.user_id,
                        record.task_type,
                        record.status,
                        record.current_node,
                        state_json,
                        created_at,
                        now,
                    ),
                )
            self._touch_live(state)
            return state

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM task_states WHERE task_id = ?",
                (state["task_id"],),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT OR REPLACE INTO task_states
                (task_id, session_id, user_id, task_type, status, current_node,
                 state_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.task_id,
                    record.session_id,
                    record.user_id,
                    record.task_type,
                    record.status,
                    record.current_node,
                    state_json,
                    created_at,
                    now,
                ),
            )
            conn.commit()
        self._touch_live(state)
        return state

    @staticmethod
    def _touch_live(state: AgentState) -> None:
        from app.services.live_task_state import touch_live_if_active

        touch_live_if_active(str(state["task_id"]), state)

    def load(self, task_id: str, *, read_only: bool = False) -> Optional[AgentState]:
        if uses_postgres():
            ctx = postgres_read_connection if read_only and postgres_uses_read_replica() else postgres_connection
            with ctx() as conn:
                row = conn.execute(
                    "SELECT state_json FROM task_states WHERE task_id = %s",
                    (task_id,),
                ).fetchone()
            if not row:
                return None
            return ensure_agent_state(json.loads(row["state_json"]))

        with self._connect() as conn:
            row = conn.execute(
                "SELECT state_json FROM task_states WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if not row:
            return None
        return ensure_agent_state(json.loads(row["state_json"]))

    def list_recent_tasks(self, limit: int = 20, *, read_only: bool = True) -> list[TaskRecord]:
        if uses_postgres():
            ctx = postgres_read_connection if read_only and postgres_uses_read_replica() else postgres_connection
            with ctx() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM task_states
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                ).fetchall()
            return self._rows_to_records(rows)

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM task_states
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return self._rows_to_records(rows)

    def delete_task(self, task_id: str) -> bool:
        if uses_postgres():
            with postgres_connection() as conn:
                row = conn.execute(
                    "DELETE FROM task_states WHERE task_id = %s RETURNING task_id",
                    (task_id,),
                ).fetchone()
            return bool(row)

        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM task_states WHERE task_id = ?",
                (task_id,),
            )
            conn.commit()
            deleted = cur.rowcount > 0
        return deleted

    def _rows_to_records(self, rows: Any) -> list[TaskRecord]:
        records: list[TaskRecord] = []
        for row in rows:
            state = json.loads(row["state_json"])
            records.append(
                TaskRecord(
                    task_id=row["task_id"],
                    session_id=row["session_id"],
                    user_id=row["user_id"],
                    task_type=row["task_type"],
                    status=row["status"],
                    current_node=row["current_node"],
                    input_payload=state.get("input_payload", {}),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    final_answer=state.get("final_answer"),
                    structured_output=state.get("structured_output"),
                    artifacts=state.get("artifacts"),
                )
            )
        return records

    def list_waiting_review_older_than(self, minutes: int) -> list[AgentState]:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        if uses_postgres():
            with postgres_connection() as conn:
                rows = conn.execute(
                    "SELECT state_json FROM task_states WHERE status = %s",
                    (TaskStatus.WAITING_REVIEW.value,),
                ).fetchall()
        else:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT state_json FROM task_states WHERE status = ?",
                    (TaskStatus.WAITING_REVIEW.value,),
                ).fetchall()

        expired: list[AgentState] = []
        for row in rows:
            state = ensure_agent_state(json.loads(row["state_json"]))
            requested_at = state.get("review_requested_at")
            if not requested_at:
                continue
            requested = datetime.fromisoformat(requested_at.replace("Z", "+00:00"))
            if requested <= cutoff:
                expired.append(state)
        return expired

    def get_task_record(self, task_id: str) -> Optional[TaskRecord]:
        if uses_postgres():
            with postgres_connection() as conn:
                row = conn.execute(
                    "SELECT * FROM task_states WHERE task_id = %s",
                    (task_id,),
                ).fetchone()
        else:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM task_states WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
        if not row:
            return None
        state = json.loads(row["state_json"])
        return TaskRecord(
            task_id=row["task_id"],
            session_id=row["session_id"],
            user_id=row["user_id"],
            task_type=row["task_type"],
            status=row["status"],
            current_node=row["current_node"],
            input_payload=state.get("input_payload", {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            final_answer=state.get("final_answer"),
            structured_output=state.get("structured_output"),
            artifacts=state.get("artifacts"),
        )


_store: StateStore | None = None
_stores: dict[str, StateStore] = {}


def get_state_store() -> StateStore:
    from app.services.tenant_storage import current_sqlite_path, tenant_cache_key

    key = tenant_cache_key()
    if key == "default":
        global _store
        if _store is None:
            _store = StateStore()
        return _store
    if key not in _stores:
        _stores[key] = StateStore(db_path=current_sqlite_path())
    return _stores[key]
