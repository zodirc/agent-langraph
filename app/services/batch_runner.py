from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from app.config.settings import settings
from app.services.batch_store import get_batch_store
from app.services.graph_runner import get_graph_runner
from app.services.input_guard import sanitize_input_payload

logger = logging.getLogger(__name__)


class BatchRunner:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=settings.BATCH_MAX_WORKERS)
        self._lock = threading.Lock()

    def submit_batch(self, batch_id: str, user_id: str) -> None:
        if settings.QUEUE_BACKEND.lower() == "celery":
            try:
                from app.services.celery_tasks import process_batch_task

                if process_batch_task is not None:
                    process_batch_task.delay(batch_id, user_id)
                    return
            except Exception as exc:
                logger.warning("Celery submit failed, using thread pool: %s", exc)
        self._executor.submit(self._process_batch, batch_id, user_id)

    def _process_batch(self, batch_id: str, user_id: str) -> None:
        store = get_batch_store()
        store.set_batch_status(batch_id, "RUNNING")
        items = store.list_pending_items(batch_id)
        runner = get_graph_runner()

        def run_item(item: dict[str, Any]) -> None:
            item_id = item["item_id"]
            try:
                payload = sanitize_input_payload(item["input_payload"])
                state = runner.start_task(
                    user_id=user_id,
                    task_type=item["task_type"],
                    input_payload=payload,
                )
                store.update_item(
                    item_id,
                    status="COMPLETED",
                    task_id=state["task_id"],
                )
            except Exception as exc:
                logger.exception("Batch item failed: %s", item_id)
                store.update_item(item_id, status="FAILED", error=str(exc))

        futures = [self._executor.submit(run_item, item) for item in items]
        for future in as_completed(futures):
            future.result()
        store.refresh_batch_counters(batch_id)
        logger.info("Batch completed", extra={"batch_id": batch_id})


_runner: Optional[BatchRunner] = None
_runner_lock = threading.Lock()


def get_batch_runner() -> BatchRunner:
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = BatchRunner()
        return _runner
