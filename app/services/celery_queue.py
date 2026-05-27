"""Redis-backed priority queue aligned with SQLite task_queue (Celery backend)."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.config.settings import settings
from app.services.queue_priority import celery_task_priority, effective_priority

logger = logging.getLogger(__name__)

_REDIS_KEY = "agent:task_queue:pending"
_client: Any = None


def _redis():
    global _client
    if _client is not None:
        return _client
    try:
        import redis

        _client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        _client.ping()
        return _client
    except Exception as exc:
        logger.warning("Celery queue Redis unavailable: %s", exc)
        return None


def uses_celery_queue() -> bool:
    return settings.QUEUE_BACKEND.lower() == "celery"


def push_pending(queue_id: str, *, priority: int, enqueued_at: str) -> None:
    """Mirror enqueue into Redis ZSET (score = effective priority)."""
    if not uses_celery_queue():
        return
    client = _redis()
    if client is None:
        return
    score = effective_priority(base_priority=priority, enqueued_at=enqueued_at)
    client.zadd(_REDIS_KEY, {queue_id: score})


def pop_highest_pending() -> Optional[str]:
    """Pop queue_id with highest effective priority."""
    client = _redis()
    if client is None:
        return None
    result = client.zpopmax(_REDIS_KEY, 1)
    if not result:
        return None
    return str(result[0][0])


def remove_pending(queue_id: str) -> None:
    client = _redis()
    if client is None:
        return
    client.zrem(_REDIS_KEY, queue_id)


def dispatch_queue_task(queue_id: str, *, effective: int) -> bool:
    """Submit Celery worker for a queue item."""
    from app.services.celery_tasks import process_queue_task

    if process_queue_task is None:
        return False
    prio = celery_task_priority(effective)
    process_queue_task.apply_async(args=[queue_id], priority=prio)
    return True
