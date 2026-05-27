from app.services.task_queue_store import get_task_queue_store


def test_task_queue_priority_order(tmp_path, monkeypatch):
    import app.services.task_queue_store as mod
    from app.config.settings import Settings

    db = str(tmp_path / "q.db")
    cfg = f"""
storage:
  sqlite_path: {db}
task_queue:
  starvation_sec: 0
"""
    path = tmp_path / "c.yaml"
    path.write_text(cfg, encoding="utf-8")
    settings = Settings(str(path))
    monkeypatch.setattr(mod, "settings", settings)

    store = mod.TaskQueueStore(db)
    low = store.enqueue(
        user_id="u1",
        task_type="qa",
        input_payload={"goal": "low"},
        priority=1,
    )
    high = store.enqueue(
        user_id="u1",
        task_type="qa",
        input_payload={"goal": "high"},
        priority=10,
    )
    first = store.dequeue_next(user_id="u1")
    assert first is not None
    assert first["queue_id"] == high
    store.mark_completed(high, task_id="t-high")
    second = store.dequeue_next(user_id="u1")
    assert second is not None
    assert second["queue_id"] == low
