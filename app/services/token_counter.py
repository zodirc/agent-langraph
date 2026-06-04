"""Local token counting for session metering when provider usage is missing."""

from __future__ import annotations

from functools import lru_cache
from typing import Any


def _chars_per_token_fallback() -> int:
    return 4


def count_text_tokens(text: str, model_name: str = "") -> int:
    """Count tokens in text; tiktoken when available, else char heuristic."""
    if not text:
        return 0
    encoding = _encoding_for_model(model_name)
    if encoding is not None:
        try:
            return len(encoding.encode(text))
        except Exception:
            pass
    return max(1, len(text) // _chars_per_token_fallback())


def count_llm_exchange_tokens(
    *,
    model_name: str,
    system_prompt: str,
    user_content: str,
    response_text: str,
) -> dict[str, int]:
    prompt_text = f"{system_prompt}\n{user_content}".strip()
    prompt_tokens = count_text_tokens(prompt_text, model_name)
    completion_tokens = count_text_tokens(response_text or "", model_name)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


@lru_cache(maxsize=32)
def _encoding_for_model(model_name: str) -> Any | None:
    try:
        import tiktoken
    except ImportError:
        return None
    name = (model_name or "").lower().strip()
    try:
        if name:
            return tiktoken.encoding_for_model(name)
    except Exception:
        pass
    for candidate in ("gpt-4o", "gpt-4", "claude-3-5-sonnet-20241022"):
        try:
            return tiktoken.encoding_for_model(candidate)
        except Exception:
            continue
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None
