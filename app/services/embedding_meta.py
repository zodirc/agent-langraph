"""Embedding model metadata and index compatibility checks."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.config.settings import settings
from app.services.embedding_service import embed_text

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingMeta:
    model_name: str
    dimension: int
    distance_metric: str
    version: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_current_embedding_meta() -> EmbeddingMeta:
    model = settings.EMBEDDING_MODEL.lower()
    dim = settings.EMBEDDING_DIMENSION or len(embed_text("probe"))
    metric = settings.EMBEDDING_DISTANCE_METRIC
    version = settings.EMBEDDING_VERSION
    return EmbeddingMeta(
        model_name=model,
        dimension=dim,
        distance_metric=metric,
        version=version,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def validate_index_compatibility(stored: EmbeddingMeta, current: EmbeddingMeta) -> bool:
    return (
        stored.dimension == current.dimension
        and stored.distance_metric == current.distance_metric
    )


def meta_from_dict(data: dict[str, Any]) -> EmbeddingMeta:
    return EmbeddingMeta(
        model_name=str(data.get("model_name", "default")),
        dimension=int(data.get("dimension", 384)),
        distance_metric=str(data.get("distance_metric", "cosine")),
        version=str(data.get("version", "v1")),
        created_at=str(data.get("created_at", "")),
    )


def record_incompatibility(stored: EmbeddingMeta, current: EmbeddingMeta) -> None:
    logger.warning(
        "embedding incompatibility: stored=%s dim=%s current=%s dim=%s",
        stored.model_name,
        stored.dimension,
        current.model_name,
        current.dimension,
    )
    if not settings.METRICS_ENABLED:
        return
    try:
        from app.services.metrics_service import get_metrics_service

        get_metrics_service().inc_embedding_incompatibility(
            stored.model_name, current.model_name
        )
    except Exception:
        pass


def serialize_meta(meta: EmbeddingMeta) -> str:
    return json.dumps(meta.to_dict(), ensure_ascii=False)
