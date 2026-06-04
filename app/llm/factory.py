"""Build LangChain chat models from resolved settings."""

from __future__ import annotations

from typing import Any

from app.llm.registry import get_provider_spec, is_openai_compat_provider, normalize_provider_id


def normalize_anthropic_base_url(base_url: str) -> str:
    """ChatAnthropic appends /v1/messages; strip suffix if config already includes it."""
    url = (base_url or "").strip().rstrip("/")
    suffix = "/v1/messages"
    if url.endswith(suffix):
        return url[: -len(suffix)] or url
    return url


def create_chat_model(
    *,
    provider: str,
    model_name: str,
    api_key: str,
    base_url: str,
    max_tokens: int,
    temperature: float,
    max_retries: int,
    timeout: int,
) -> Any:
    """
    Instantiate a LangChain chat model for the configured provider.

    Business code should call ``get_llm()`` in ``llm_client`` rather than this directly.
    """
    pid = normalize_provider_id(provider)
    spec = get_provider_spec(pid)

    if spec.api_family == "anthropic_messages":
        from langchain_anthropic import ChatAnthropic

        normalized_url = normalize_anthropic_base_url(base_url) or None
        return ChatAnthropic(
            model=model_name,
            api_key=api_key,
            base_url=normalized_url,
            max_tokens=max_tokens,
            temperature=temperature,
            max_retries=max_retries,
            timeout=timeout,
            streaming=True,
        )

    if is_openai_compat_provider(pid):
        from langchain_openai import ChatOpenAI

        openai_url = (base_url or "").strip().rstrip("/") or None
        return ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url=openai_url,
            max_tokens=max_tokens,
            temperature=temperature,
            max_retries=max_retries,
            timeout=timeout,
            streaming=True,
            stream_usage=True,
        )

    raise ValueError(f"Unsupported provider api_family for {provider!r}")
