import json
import sqlite3
from pathlib import Path

from app.services.audit_store import AuditStore
from app.services.memory_store import MemoryStore
from app.services.task_cleanup import (
    delete_checkpoints_for_task,
    purge_task_remains,
)


def test_purge_task_remains_clears_audit_memory_artifacts(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    artifacts = tmp_path / "artifacts"
    monkeypatch.setattr("app.services.artifact_tools.settings.ARTIFACTS_PATH", str(artifacts))
    monkeypatch.setattr("app.services.audit_store.settings.SQLITE_PATH", str(db_path))
    monkeypatch.setattr("app.services.memory_store.settings.SQLITE_PATH", str(db_path))
    monkeypatch.setattr("app.services.audit_store.uses_postgres", lambda: False)
    monkeypatch.setattr("app.services.memory_store.uses_postgres", lambda: False)

    task_id = "sess-purge-001"
    art = artifacts / task_id
    art.mkdir(parents=True)
    (art / "novel.txt").write_text("x", encoding="utf-8")

    audit = AuditStore(db_path=str(db_path))
    audit.append_events(task_id, [{"node": "planning", "action": "ok"}])
    assert len(audit.get_chain(task_id)) == 1

    from app.domain.memory import MemoryRecord
    from datetime import datetime, timezone

    mem = MemoryStore(db_path=str(db_path))
    mem.write(
        MemoryRecord(
            memory_id="m1",
            task_id=task_id,
            user_id="u1",
            task_type="qa",
            summary="hello",
            tags=[],
            payload={},
            created_at=datetime.now(timezone.utc).isoformat(),
            session_id=task_id,
        )
    )

    deleted_threads: list[str] = []

    class FakeCp:
        def delete_thread(self, thread_id: str) -> None:
            deleted_threads.append(thread_id)

    monkeypatch.setattr(
        "app.runtime.checkpointer.create_checkpointer",
        lambda: FakeCp(),
    )
    monkeypatch.setattr(
        "app.services.task_cleanup.settings.CHECKPOINT_BACKEND",
        "sqlite",
    )
    monkeypatch.setattr(
        "app.services.task_cleanup.settings.CHECKPOINT_SQLITE_PATH",
        str(tmp_path / "empty_cp.db"),
    )

    result = purge_task_remains(task_id, session_turn_hint=2)

    assert result["artifacts_dir"] is True
    assert not art.exists()
    assert result["audit_events_removed"] == 1
    assert audit.get_chain(task_id) == []
    assert result["memories_removed"] == 1
    assert "sess-purge-001:t1" in deleted_threads
    assert "sess-purge-001:t2" in deleted_threads


def test_delete_checkpoints_scans_sqlite_db(tmp_path, monkeypatch):
    cp_path = tmp_path / "cp.db"
    conn = sqlite3.connect(str(cp_path))
    from langgraph.checkpoint.sqlite import SqliteSaver

    SqliteSaver(conn).setup()
    conn.execute(
        "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, type, checkpoint, metadata) "
        "VALUES (?, '', 'c1', 'json', X'00', X'00')",
        ("orphan-task:t9",),
    )
    conn.commit()
    conn.close()

    deleted: list[str] = []

    class FakeCp:
        def delete_thread(self, thread_id: str) -> None:
            deleted.append(thread_id)

    monkeypatch.setattr(
        "app.runtime.checkpointer.create_checkpointer",
        lambda: FakeCp(),
    )
    monkeypatch.setattr("app.services.task_cleanup.settings.CHECKPOINT_BACKEND", "sqlite")
    monkeypatch.setattr(
        "app.services.task_cleanup.settings.CHECKPOINT_SQLITE_PATH",
        str(cp_path),
    )

    n = delete_checkpoints_for_task("orphan-task", max_turn_hint=1)
    assert n >= 1
    assert "orphan-task:t9" in deleted
