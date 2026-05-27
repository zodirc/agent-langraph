import time

import pytest

from app.services.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    classify_llm_error,
    is_retryable_category,
    reset_circuit_breakers,
)
from app.services.llm_client import RetryableError


@pytest.fixture(autouse=True)
def _reset():
    reset_circuit_breakers()
    yield
    reset_circuit_breakers()


def test_circuit_opens_after_threshold():
    cb = CircuitBreaker("test", failure_threshold=3)
    for _ in range(3):
        cb.record_failure()
    assert cb.state == CircuitState.OPEN


def test_open_circuit_rejects_calls():
    cb = CircuitBreaker("test", failure_threshold=1)
    cb.record_failure()
    with pytest.raises(CircuitOpenError):
        cb.allow_call()


def test_half_open_success_closes():
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.01)
    cb.record_failure()
    time.sleep(0.02)
    cb.allow_call()
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_error_classification():
    assert classify_llm_error(RetryableError("429 rate limit")).value == "rate_limit"
    assert is_retryable_category(classify_llm_error(RetryableError("503"))) is True
