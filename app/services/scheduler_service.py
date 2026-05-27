from __future__ import annotations

import logging
from typing import Any, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config.settings import settings
from app.services.input_guard import sanitize_input_payload
from app.services.schedule_store import get_schedule_store
from app.services.task_queue_store import get_task_queue_store

logger = logging.getLogger(__name__)


class SchedulerService:
    def __init__(self) -> None:
        self._scheduler = BackgroundScheduler(timezone="UTC")
        self._job_map: dict[str, str] = {}

    def start(self) -> None:
        if not settings.SCHEDULER_ENABLED:
            logger.info("Scheduler disabled by configuration")
            return
        if self._scheduler.running:
            return
        self._scheduler.start()
        self.reload_jobs()
        poll_sec = int(getattr(settings, "QUEUE_POLL_SECONDS", 10))
        if poll_sec > 0:
            self._scheduler.add_job(
                self._process_task_queue,
                "interval",
                seconds=poll_sec,
                id="task-queue-poller",
                replace_existing=True,
            )
        if settings.CHECKPOINT_CLEANUP_ENABLED:
            self._scheduler.add_job(
                self._run_checkpoint_cleanup,
                CronTrigger(hour=3, minute=0),
                id="checkpoint-cleanup",
                replace_existing=True,
            )
        logger.info("Scheduler started", extra={"jobs": len(self._job_map)})

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def reload_jobs(self) -> None:
        for job_id in list(self._job_map.values()):
            try:
                self._scheduler.remove_job(job_id)
            except Exception:
                pass
        self._job_map.clear()

        for schedule in get_schedule_store().list_enabled():
            self._register_job(schedule)

    def _register_job(self, schedule: dict[str, Any]) -> None:
        schedule_id = schedule["schedule_id"]
        try:
            trigger = CronTrigger.from_crontab(schedule["cron_expression"])
        except ValueError as exc:
            logger.error("Invalid cron for schedule %s: %s", schedule_id, exc)
            return

        job = self._scheduler.add_job(
            self._run_scheduled_task,
            trigger=trigger,
            id=f"schedule-{schedule_id}",
            kwargs={"schedule_id": schedule_id},
            replace_existing=True,
        )
        self._job_map[schedule_id] = job.id
        next_run = job.next_run_time.isoformat() if job.next_run_time else None
        get_schedule_store().set_next_run_hint(schedule_id, next_run)

    def _run_scheduled_task(self, schedule_id: str) -> None:
        store = get_schedule_store()
        schedule = store.get(schedule_id)
        if not schedule or not schedule.get("enabled"):
            return
        try:
            payload = sanitize_input_payload(schedule["input_payload"])
            queue_id = get_task_queue_store().enqueue(
                user_id=schedule["user_id"],
                task_type=schedule["task_type"],
                input_payload=payload,
                priority=int(schedule.get("priority", 0)),
                source="schedule",
                source_ref=schedule_id,
            )
            job = self._scheduler.get_job(self._job_map.get(schedule_id, ""))
            next_run = job.next_run_time.isoformat() if job and job.next_run_time else None
            store.mark_run(schedule_id, next_run_hint=next_run)
            logger.info(
                "Scheduled task enqueued",
                extra={"schedule_id": schedule_id, "queue_id": queue_id},
            )
        except Exception as exc:
            logger.exception("Scheduled task enqueue failed: %s", schedule_id)
            store.mark_run(schedule_id, next_run_hint=f"error: {exc}")

    def _run_checkpoint_cleanup(self) -> None:
        from app.services.checkpoint_cleanup import cleanup_old_checkpoints

        try:
            removed = cleanup_old_checkpoints()
            logger.info("Checkpoint cleanup finished", extra={"removed_approx": removed})
        except Exception as exc:
            logger.exception("Checkpoint cleanup failed: %s", exc)

    def _process_task_queue(self) -> None:
        if settings.QUEUE_BACKEND.lower() == "celery":
            return
        from app.services.graph_runner import get_graph_runner

        queue = get_task_queue_store()
        max_per_tick = int(getattr(settings, "QUEUE_MAX_PER_TICK", 4))
        runner = get_graph_runner()
        for _ in range(max_per_tick):
            item = queue.dequeue_next()
            if not item:
                break
            qid = item["queue_id"]
            try:
                payload = sanitize_input_payload(item["input_payload"])
                state = runner.start_task(
                    user_id=item["user_id"],
                    task_type=item["task_type"],
                    input_payload=payload,
                )
                queue.mark_completed(qid, task_id=state["task_id"])
            except Exception as exc:
                logger.exception("Queue task failed: %s", qid)
                queue.mark_failed(qid, error=str(exc))

    def add_schedule(self, schedule: dict[str, Any]) -> None:
        if self._scheduler.running:
            self._register_job(schedule)

    def remove_schedule(self, schedule_id: str) -> None:
        job_id = self._job_map.pop(schedule_id, None)
        if job_id and self._scheduler.running:
            try:
                self._scheduler.remove_job(job_id)
            except Exception:
                pass


_service: Optional[SchedulerService] = None


def get_scheduler_service() -> SchedulerService:
    global _service
    if _service is None:
        _service = SchedulerService()
    return _service
