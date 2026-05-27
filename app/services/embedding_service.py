from __future__ import annotations

import hashlib
import logging
import math
from typing import Any

import httpx

from app.config.settings import settings

logger = logging.getLogger(__name__)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Generate embeddings (Voyage API or deterministic local fallback)."""
    if not texts:
        return []
    model = settings.EMBEDDING_MODEL.lower()
    if model in ("voyage-3", "voyage-3-lite") and settings.EMBEDDING_API_KEY:
        try:
            return _voyage_embed(texts, model)
        except Exception as exc:
            logger.warning("Voyage embedding failed, using local fallback: %s", exc)
    return [_local_embedding(text) for text in texts]


def embed_text(text: str) -> list[float]:
    return embed_texts([text])[0]


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
