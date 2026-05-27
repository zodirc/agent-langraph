"""Factual runtime limits from settings — injected into get_runtime_info for the LLM."""

from __future__ import annotations

from typing import Any

from app.config.settings import settings


def build_runtime_limits() -> dict[str, Any]:
    from app.services.llm_capabilities import build_model_capabilities

    caps = build_model_capabilities()
    limits = {
        "model_max_tokens_planning": settings.MODEL_MAX_TOKENS_PLANNING,
        "model_max_tokens_reasoning": settings.MODEL_MAX_TOKENS_REASONING,
        "model_max_tokens_writing": settings.MODEL_MAX_TOKENS_WRITING,
        "artifact_chunk_chars": settings.ARTIFACT_CHUNK_CHARS,
        "artifact_max_chunks_per_turn": settings.ARTIFACT_MAX_CHUNKS_PER_TURN,
        "artifact_max_chars_per_turn": settings.ARTIFACT_MAX_CHARS_PER_TURN,
        "artifact_max_write_bytes": settings.ARTIFACT_MAX_WRITE_BYTES,
        "artifact_max_file_bytes": settings.ARTIFACT_MAX_FILE_BYTES,
    }
    limits["structured_output_preferred"] = caps.get("structured_output", {})
    limits["content_block_types"] = caps.get("content_block_types", {})
    return limits
