"""Millisecond-level retrieval / reasoning stage timing (§7.5 P0 instrumentation)."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

from app.config.settings import settings

logger = logging.getLogger(__name__)


def retrieval_timing_enabled() -> bool:
    return bool(getattr(settings, "RETRIEVAL_TIMING_ENABLED", True))


@contextmanager
def timed_stage(
    scope: str,
    stage: str,
    *,
    extra: dict[str, Any] | None = None,
) -> Iterator[dict[str, float]]:
    """Record elapsed ms for a named stage; yields a mutable timings dict."""
    timings: dict[str, float] = {}
    if not retrieval_timing_enabled():
        yield timings
        return
    started = time.perf_counter()
    try:
        yield timings
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        timings[stage] = elapsed_ms
        payload = {"scope": scope, "stage": stage, "elapsed_ms": round(elapsed_ms, 2)}
        if extra:
            payload.update(extra)
        logger.info("retrieval_timing %s/%s %.1fms", scope, stage, elapsed_ms, extra=payload)
        _observe(scope, stage, elapsed_ms)


def record_timing(scope: str, stage: str, elapsed_ms: float, **extra: Any) -> None:
    if not retrieval_timing_enabled():
        return
    logger.info(
        "retrieval_timing %s/%s %.1fms",
        scope,
        stage,
        elapsed_ms,
        extra={"scope": scope, "stage": stage, "elapsed_ms": round(elapsed_ms, 2), **extra},
    )
    _observe(scope, stage, elapsed_ms)


def _observe(scope: str, stage: str, elapsed_ms: float) -> None:
    try:
        if not settings.METRICS_ENABLED:
            return
        from app.services.metrics_service import get_metrics_service

        get_metrics_service().observe_retrieval_stage_ms(scope, stage, elapsed_ms)
    except Exception:
        pass
