"""LLM circuit breaker and error classification."""

from __future__ import annotations

import enum
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from app.config.settings import settings

F = TypeVar("F", bound=Callable[..., Any])


class CircuitState(str, enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Circuit is open; LLM calls should not be attempted."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"LLM circuit breaker open: {name}")


class LLMErrorCategory(str, enum.Enum):
    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    AUTH_ERROR = "auth_error"
    CONTEXT_TOO_LONG = "context_too_long"
    CONTENT_FILTER = "content_filter"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


def classify_llm_error(exc: BaseException) -> LLMErrorCategory:
    message = str(exc).lower()
    name = type(exc).__name__.lower()
    if "auth" in message or "401" in message or "403" in message or "authentication" in name:
        return LLMErrorCategory.AUTH_ERROR
    if "content" in message and ("filter" in message or "policy" in message):
        return LLMErrorCategory.CONTENT_FILTER
    if "context" in message and ("length" in message or "too long" in message or "token" in message):
        return LLMErrorCategory.CONTEXT_TOO_LONG
    if "timeout" in message or "timed out" in message:
        return LLMErrorCategory.TIMEOUT
    if "429" in message or "rate" in message or "limit" in message:
        return LLMErrorCategory.RATE_LIMIT
    if (
        "incomplete chunked" in message
        or "peer closed" in message
        or "connection reset" in message
        or "broken pipe" in message
    ):
        return LLMErrorCategory.SERVER_ERROR
    if any(code in message for code in ("500", "502", "503", "529", "server error")):
        return LLMErrorCategory.SERVER_ERROR
    return LLMErrorCategory.UNKNOWN


def is_retryable_category(category: LLMErrorCategory) -> bool:
    return category in (
        LLMErrorCategory.RATE_LIMIT,
        LLMErrorCategory.SERVER_ERROR,
        LLMErrorCategory.TIMEOUT,
    )


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 5
    recovery_timeout: float = 60.0
    half_open_max_calls: int = 1
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0
    half_open_calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _maybe_transition(self) -> None:
        if self.state != CircuitState.OPEN:
            return
        if time.monotonic() - self.last_failure_time >= self.recovery_timeout:
            self.state = CircuitState.HALF_OPEN
            self.half_open_calls = 0

    def record_success(self) -> None:
        with self._lock:
            self.failure_count = 0
            self.state = CircuitState.CLOSED
            self.half_open_calls = 0
            _emit_circuit_metric(self.name, self.state)

    def record_failure(self) -> None:
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.monotonic()
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.OPEN
            elif self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
            _emit_circuit_metric(self.name, self.state)

    def allow_call(self) -> None:
        with self._lock:
            self._maybe_transition()
            if self.state == CircuitState.OPEN:
                raise CircuitOpenError(self.name)
            if self.state == CircuitState.HALF_OPEN:
                if self.half_open_calls >= self.half_open_max_calls:
                    raise CircuitOpenError(self.name)
                self.half_open_calls += 1

    def call(self, func: F, *args: Any, **kwargs: Any) -> Any:
        self.allow_call()
        try:
            result = func(*args, **kwargs)
            self.record_success()
            return result
        except Exception:
            self.record_failure()
            raise


_breakers: dict[str, CircuitBreaker] = {}
_breakers_lock = threading.Lock()


def _emit_circuit_metric(name: str, state: CircuitState) -> None:
    if not settings.METRICS_ENABLED:
        return
    try:
        from app.services.metrics_service import get_metrics_service

        get_metrics_service().set_llm_circuit_state(name, state.value)
    except Exception:
        pass


def get_llm_circuit_breaker(name: str = "default") -> CircuitBreaker:
    with _breakers_lock:
        breaker = _breakers.get(name)
        if breaker is None:
            breaker = CircuitBreaker(
                name=name,
                failure_threshold=settings.LLM_CIRCUIT_FAILURE_THRESHOLD,
                recovery_timeout=settings.LLM_CIRCUIT_RECOVERY_TIMEOUT_SEC,
            )
            _breakers[name] = breaker
        return breaker


def reset_circuit_breakers() -> None:
    with _breakers_lock:
        _breakers.clear()
