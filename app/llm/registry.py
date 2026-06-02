"""Provider registry — maps config ``model.provider`` to API family and defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

ApiFamily = Literal["anthropic_messages", "openai_chat"]


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    api_family: ApiFamily
    default_base_url: str
    secret_env_keys: tuple[str, ...]
    default_model: str


# Aliases collapse to canonical ids for factory routing.
_PROVIDER_ALIASES: dict[str, str] = {
    "zhipu": "glm",
    "zhipuai": "glm",
    "openai_compat": "openai_compat",
    "compatible": "openai_compat",
    "custom": "openai_compat",
}

_PROVIDERS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(
        id="anthropic",
        api_family="anthropic_messages",
        default_base_url="https://api.anthropic.com",
        secret_env_keys=("ANTHROPIC_API_KEY",),
        default_model="claude-sonnet-4-5",
    ),
    "openai": ProviderSpec(
        id="openai",
        api_family="openai_chat",
        default_base_url="https://api.openai.com/v1",
        secret_env_keys=("OPENAI_API_KEY",),
        default_model="gpt-4o",
    ),
    "deepseek": ProviderSpec(
        id="deepseek",
        api_family="openai_chat",
        default_base_url="https://api.deepseek.com",
        secret_env_keys=("DEEPSEEK_API_KEY",),
        default_model="deepseek-v4-flash",
    ),
    "glm": ProviderSpec(
        id="glm",
        api_family="openai_chat",
        default_base_url="https://open.bigmodel.cn/api/paas/v4/",
        secret_env_keys=("ZHIPUAI_API_KEY", "GLM_API_KEY"),
        default_model="glm-4.7",
    ),
    "openai_compat": ProviderSpec(
        id="openai_compat",
        api_family="openai_chat",
        default_base_url="",
        secret_env_keys=("MODEL_API_KEY", "OPENAI_API_KEY"),
        default_model="",
    ),
}


def normalize_provider_id(provider: str) -> str:
    key = (provider or "anthropic").strip().lower()
    return _PROVIDER_ALIASES.get(key, key)


def get_provider_spec(provider: str) -> ProviderSpec:
    pid = normalize_provider_id(provider)
    spec = _PROVIDERS.get(pid)
    if spec is None:
        raise ValueError(
            f"Unknown model provider {provider!r}. "
            f"Supported: {', '.join(sorted(_PROVIDERS))}"
        )
    return spec


def is_openai_compat_provider(provider: str) -> bool:
    return get_provider_spec(provider).api_family == "openai_chat"


def resolve_model_api_key(
    provider: str,
    secrets: Any,
    *,
    yaml_api_key: str = "",
) -> str:
    """Resolve API key: provider-specific env → MODEL_API_KEY → yaml."""
    spec = get_provider_spec(provider)
    for key in (*spec.secret_env_keys, "MODEL_API_KEY"):
        value = secrets.get(key) if secrets is not None else None
        if value and str(value).strip():
            return str(value).strip()
    return (yaml_api_key or "").strip()


_PROVIDER_BASE_URL_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_BASE_URL",
    "openai": "OPENAI_BASE_URL",
    "deepseek": "DEEPSEEK_BASE_URL",
    "glm": "GLM_BASE_URL",
}


def resolve_model_base_url(provider: str, configured: str) -> str:
    """Use explicit base_url from config/env; else provider-specific env; else default."""
    url = (configured or "").strip().rstrip("/")
    if url:
        return url
    pid = normalize_provider_id(provider)
    env_key = _PROVIDER_BASE_URL_ENV.get(pid)
    if env_key:
        from_env = (os.environ.get(env_key) or "").strip().rstrip("/")
        if from_env:
            return from_env
    spec = get_provider_spec(provider)
    return spec.default_base_url.rstrip("/")


def default_model_name(provider: str, configured: str) -> str:
    name = (configured or "").strip()
    if name:
        return name
    return get_provider_spec(provider).default_model
