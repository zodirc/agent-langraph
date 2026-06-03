"""图执行背压
graph_runner._run_with_slot wraps start_task

Bounded graph concurrency: acquire slot or GraphExecutionRejected (HTTP 429).
stream worker invoke."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator

from app.config.settings import settings


class GraphExecutionRejected(Exception):
    """Raised when the execution pool is saturated and acquire times out."""

    def __init__(self, *, active: int, max_concurrent: int, waited_sec: float) -> None:
        self.active = active
        self.max_concurrent = max_concurrent
        self.waited_sec = waited_sec
        super().__init__(
            f"graph execution pool saturated ({active}/{max_concurrent}), "
            f"waited {waited_sec:.1f}s"
        )


class GraphExecutionPool:
    def __init__(self, *, max_concurrent: int, acquire_timeout_sec: float) -> None:
        self._max = max(1, max_concurrent)
        self._timeout = max(0.0, acquire_timeout_sec)
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._active = 0
        self._waiting = 0
        self._rejected_total = 0

    @property
    def max_concurrent(self) -> int:
        return self._max

    def stats(self) -> dict[str, int | float]:
        with self._lock:
            return {
                "active": self._active,
                "waiting": self._waiting,
                "max_concurrent": self._max,
                "rejected_total": self._rejected_total,
            }

    @contextmanager
    def acquire(self) -> Iterator[None]:
        if not settings.GRAPH_RUNNER_BACKPRESSURE_ENABLED:
            yield
            return

        metrics = None
        if settings.METRICS_ENABLED:
            from app.services.metrics_service import get_metrics_service

            metrics = get_metrics_service()
        deadline = time.monotonic() + self._timeout if self._timeout > 0 else None

        with self._lock:
            while self._active >= self._max:
                self._waiting += 1
                if metrics is not None:
                    metrics.observe_graph_queue(
                        active=self._active,
                        waiting=self._waiting,
                        max_concurrent=self._max,
                    )
                remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                if remaining is not None and remaining <= 0:
                    self._waiting -= 1
                    self._rejected_total += 1
                    if metrics is not None:
                        metrics.inc_graph_rejected()
                    raise GraphExecutionRejected(
                        active=self._active,
                        max_concurrent=self._max,
                        waited_sec=self._timeout,
                    )
                self._cond.wait(timeout=remaining if remaining is not None else self._timeout or 1.0)
                self._waiting -= 1
                if self._active < self._max:
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    self._rejected_total += 1
                    if metrics is not None:
                        metrics.inc_graph_rejected()
                    raise GraphExecutionRejected(
                        active=self._active,
                        max_concurrent=self._max,
                        waited_sec=self._timeout,
                    )
            self._active += 1
            if metrics is not None:
                metrics.observe_graph_queue(
                    active=self._active,
                    waiting=self._waiting,
                    max_concurrent=self._max,
                )

        try:
            yield
        finally:
            with self._lock:
                self._active = max(0, self._active - 1)
                self._cond.notify()
                if metrics is not None:
                    metrics.observe_graph_queue(
                        active=self._active,
                        waiting=self._waiting,
                        max_concurrent=self._max,
                    )


_pool: GraphExecutionPool | None = None


def get_graph_execution_pool() -> GraphExecutionPool:
    global _pool
    if _pool is None:
        _pool = GraphExecutionPool(
            max_concurrent=settings.GRAPH_RUNNER_MAX_CONCURRENT,
            acquire_timeout_sec=settings.GRAPH_RUNNER_QUEUE_TIMEOUT_SEC,
        )
    return _pool


def reset_graph_execution_pool() -> None:
    global _pool
    _pool = None
