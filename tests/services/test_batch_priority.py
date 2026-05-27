from app.services.batch_store import BatchStore


def test_batch_pending_sorted_by_priority(tmp_path):
    db = str(tmp_path / "batch.db")
    store = BatchStore(db)
    batch_id, _ = store.create_batch(
        user_id="u1",
        items=[
            {"task_type": "qa", "input_payload": {"goal": "a"}, "priority": 1},
            {"task_type": "qa", "input_payload": {"goal": "b"}, "priority": 5},
        ],
    )
    pending = store.list_pending_items(batch_id)
    assert pending[0]["input_payload"]["goal"] == "b"
    assert pending[1]["input_payload"]["goal"] == "a"
