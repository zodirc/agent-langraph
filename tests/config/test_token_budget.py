"""Token budget configuration for LLM purposes."""

from __future__ import annotations

from app.config.settings import settings


def test_routing_token_budget_is_2048():
    assert settings.MODEL_MAX_TOKENS_ROUTING == 2048


def test_routing_timeout_is_shorter_than_default():
    assert settings.MODEL_TIMEOUT_ROUTING <= settings.MODEL_TIMEOUT
    assert settings.MODEL_TIMEOUT_ROUTING == 20
