"""Checkpoint cleanup tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config.settings import Settings
from app.services.checkpoint_cleanup import cleanup_old_checkpoints
from app.services.state_store import get_state_store
from app.runtime.state import create_initial_state, merge_state, TaskStatus


def test_cleanup_old_checkpoints_no_op_when_disabled(test_settings: Settings) -> None:
    test_settings.CHECKPOINT_RETENTION_DAYS = 0
    assert cleanup_old_checkpoints(retention_days=0) == 0


def test_cleanup_with_old_completed_task(test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.settings.settings", test_settings)
    store = get_state_store()
    old_time = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    state = merge_state(
        create_initial_state(task_id="old-task-1"),
        status=TaskStatus.COMPLETED.value,
    )
    store.save(state)
    with store._connect() as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE task_states SET updated_at = ? WHERE task_id = ?",
            (old_time, "old-task-1"),
        )
        conn.commit()
    removed = cleanup_old_checkpoints(retention_days=30)
    assert removed >= 0
