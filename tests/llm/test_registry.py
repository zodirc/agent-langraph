from __future__ import annotations

from app.llm.factory import normalize_anthropic_base_url
from app.llm.registry import (
    get_provider_spec,
    normalize_provider_id,
    resolve_model_api_key,
    resolve_model_base_url,
)


class _Secrets:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    def get(self, key: str) -> str | None:
        return self._mapping.get(key)


def test_normalize_provider_aliases():
    assert normalize_provider_id("Zhipu") == "glm"
    assert normalize_provider_id("openai_compat") == "openai_compat"


def test_resolve_api_key_provider_specific():
    secrets = _Secrets({"DEEPSEEK_API_KEY": "sk-ds", "ANTHROPIC_API_KEY": "sk-ant"})
    assert resolve_model_api_key("deepseek", secrets) == "sk-ds"
    assert resolve_model_api_key("anthropic", secrets) == "sk-ant"


def test_resolve_api_key_model_api_key_fallback():
    secrets = _Secrets({"MODEL_API_KEY": "sk-generic"})
    assert resolve_model_api_key("openai", secrets) == "sk-generic"


def test_resolve_base_url_default_per_provider():
    assert resolve_model_base_url("deepseek", "") == "https://api.deepseek.com"
    assert resolve_model_base_url("glm", "") == "https://open.bigmodel.cn/api/paas/v4"
    assert resolve_model_base_url("deepseek", "https://custom.example") == "https://custom.example"


def test_get_provider_spec_openai_family():
    assert get_provider_spec("deepseek").api_family == "openai_chat"
    assert get_provider_spec("anthropic").api_family == "anthropic_messages"


def test_normalize_anthropic_base_url_strips_messages_suffix():
    assert (
        normalize_anthropic_base_url("https://proxy.example/v1/messages")
        == "https://proxy.example"
    )
    assert normalize_anthropic_base_url("https://proxy.example") == "https://proxy.example"
