"""向量嵌入
knowledge upsert/search 调用 embed_texts；模型由 EMBEDDING_MODEL 配置。

Text embeddings: OpenAI-compatible HTTP API, Voyage, local MiniLM, or hash fallback."""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Any

import httpx

from app.config.settings import settings

logger = logging.getLogger(__name__)
_sentence_transformer_model: Any = None

_OPENAI_COMPAT_MODELS = frozenset({"openai_compat", "openai", "http", "local_http"})


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for the configured backend."""
    if not texts:
        return []
    model = settings.EMBEDDING_MODEL.lower()
    if model in _OPENAI_COMPAT_MODELS and settings.EMBEDDING_BASE_URL:
        try:
            return _openai_compat_embed(texts)
        except Exception as exc:
            logger.warning("OpenAI-compatible embedding failed, using local fallback: %s", exc)
    if model in ("voyage-3", "voyage-3-lite") and settings.EMBEDDING_API_KEY:
        try:
            return _voyage_embed(texts, model)
        except Exception as exc:
            logger.warning("Voyage embedding failed, using local fallback: %s", exc)
    if model in ("local_minilm", "all-minilm-l6-v2", "mini_lm"):
        try:
            return _local_minilm_embed(texts)
        except Exception as exc:
            logger.warning("Local MiniLM embedding failed, using hash fallback: %s", exc)
    return [_local_embedding(text) for text in texts]


def embed_text(text: str) -> list[float]:
    return embed_texts([text])[0]


def _embeddings_endpoint_url(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    if base.endswith("/embeddings"):
        return base
    if base.endswith("/v1"):
        return f"{base}/embeddings"
    return f"{base}/v1/embeddings"


def _openai_compat_embed(texts: list[str]) -> list[list[float]]:
    """Call a local or remote OpenAI-compatible /v1/embeddings endpoint."""
    base = (settings.EMBEDDING_BASE_URL or "").strip()
    if not base:
        raise ValueError("EMBEDDING_BASE_URL is required for openai_compat embedding")
    url = _embeddings_endpoint_url(base)

    api_model = (settings.EMBEDDING_API_MODEL or settings.EMBEDDING_MODEL or "").strip()
    if not api_model or api_model in _OPENAI_COMPAT_MODELS:
        api_model = "default"

    headers = {"Content-Type": "application/json"}
    if settings.EMBEDDING_API_KEY:
        headers["Authorization"] = f"Bearer {settings.EMBEDDING_API_KEY}"

    response = httpx.post(
        url,
        headers=headers,
        json={"input": texts, "model": api_model},
        timeout=float(settings.EMBEDDING_TIMEOUT_SEC or 60.0),
    )
    response.raise_for_status()
    data = response.json()
    items = sorted(data.get("data", []), key=lambda item: item.get("index", 0))
    return [list(item["embedding"]) for item in items]


def _voyage_embed(texts: list[str], model: str) -> list[list[float]]:
    response = httpx.post(
        "https://api.voyageai.com/v1/embeddings",
        headers={
            "Authorization": f"Bearer {settings.EMBEDDING_API_KEY}",
            "Content-Type": "application/json",
        },
        json={"input": texts, "model": model},
        timeout=60.0,
    )
    response.raise_for_status()
    data = response.json()
    items = sorted(data.get("data", []), key=lambda item: item.get("index", 0))
    return [list(item["embedding"]) for item in items]


def _local_embedding(text: str, dim: int = 384) -> list[float]:
    """Deterministic embedding for offline/test (architecture §23.2 fallback)."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values: list[float] = []
    for i in range(dim):
        byte = digest[i % len(digest)]
        values.append((byte / 255.0) * 2.0 - 1.0)
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


def _get_sentence_transformer():
    global _sentence_transformer_model
    if _sentence_transformer_model is not None:
        return _sentence_transformer_model
    from sentence_transformers import SentenceTransformer

    model_name = settings.LOCAL_EMBEDDING_MODEL_NAME or "sentence-transformers/all-MiniLM-L6-v2"
    _sentence_transformer_model = SentenceTransformer(
        model_name,
        device=settings.LOCAL_EMBEDDING_DEVICE or "cpu",
        cache_folder=settings.LOCAL_EMBEDDING_CACHE_DIR or None,
    )
    return _sentence_transformer_model


def _local_minilm_embed(texts: list[str]) -> list[list[float]]:
    model = _get_sentence_transformer()
    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return [vec.astype(float).tolist() for vec in vectors]


class EmbeddingFunction:
    """Chroma-compatible embedding callable."""

    def __call__(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        return embed_texts(input)

    def name(self) -> str:
        return settings.EMBEDDING_MODEL

    def embed_query(self, input: Any) -> list[float]:  # noqa: A002
        if isinstance(input, list):
            return embed_texts(input)[0]
        return embed_text(str(input))
