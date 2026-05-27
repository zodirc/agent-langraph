from __future__ import annotations

import logging

from app.services.batch_runner import get_batch_runner
from app.services.celery_app import get_celery_app

logger = logging.getLogger(__name__)

celery = get_celery_app()

if celery is not None:

    @celery.task(name="app.services.celery_tasks.process_batch")
    def process_batch_task(batch_id: str, user_id: str) -> str:
        get_batch_runner()._process_batch(batch_id, user_id)  # noqa: SLF001
        return batch_id

    @celery.task(name="app.services.celery_tasks.process_queue")
    def process_queue_task(queue_id: str) -> str:
        from app.services.graph_runner import get_graph_runner
        from app.services.input_guard import sanitize_input_payload
        from app.services.task_queue_store import get_task_queue_store

        store = get_task_queue_store()
        with store._connect() as conn:  # noqa: SLF001
            row = conn.execute(
                "SELECT * FROM task_queue WHERE queue_id = ?",
                (queue_id,),
            ).fetchone()
        if not row or row["status"] not in ("PENDING", "RUNNING"):
            return queue_id
        if row["status"] == "PENDING":
            store._claim_by_id(queue_id)  # noqa: SLF001
        item = store._row_to_item(row)
        try:
            payload = sanitize_input_payload(item["input_payload"])
            state = get_graph_runner().start_task(
                user_id=item["user_id"],
                task_type=item["task_type"],
                input_payload=payload,
            )
            store.mark_completed(queue_id, task_id=state["task_id"])
        except Exception as exc:
            logger.exception("Celery queue task failed: %s", queue_id)
            store.mark_failed(queue_id, error=str(exc))
        return queue_id

else:
    process_batch_task = None  # type: ignore[assignment,misc]
    process_queue_task = None  # type: ignore[assignment,misc]
