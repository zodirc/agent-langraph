from __future__ import annotations

from app.config.settings import settings

_celery = None


def get_celery_app():
    """Lazy Celery app for distributed queue (§21, optional)."""
    global _celery
    if _celery is not None:
        return _celery
    if settings.QUEUE_BACKEND.lower() != "celery":
        return None
    try:
        from celery import Celery

        _celery = Celery(
            "agent_langraph",
            broker=settings.REDIS_URL,
            backend=settings.REDIS_URL,
        )
        _celery.conf.task_routes = {"app.services.celery_tasks.*": {"queue": "agent"}}
        _celery.conf.broker_transport_options = {
            "priority_steps": list(range(10)),
            "sep": ":",
            "queue_order_strategy": "priority",
        }
        _celery.conf.task_default_priority = 5
        return _celery
    except ImportError:
        return None
