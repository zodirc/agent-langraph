"""
Model capabilities — factual limits for adapter selection (from settings, not NLP).
"""

from __future__ import annotations

from typing import Any

from app.config.settings import settings
from app.services.runtime_limits import build_runtime_limits


def build_model_capabilities() -> dict[str, Any]:
    """Capabilities snapshot for get_runtime_info and LLM gateway."""
    purposes = getattr(settings, "LLM_PURPOSE_CONFIG", {}) or {}
    writing_cfg = purposes.get("writing", {}) if isinstance(purposes, dict) else {}

    return {
        "model_name": settings.MODEL_NAME,
        "model_provider": settings.MODEL_PROVIDER,
        "model_enabled": settings.MODEL_ENABLED,
        "content_block_types": {
            "text": "usable_for_structured_output",
            "thinking": "metadata_only",
            "redacted_thinking": "metadata_only",
            "tool_use": "structured_output_primary",
        },
        "structured_output": {
            "preferred": str(writing_cfg.get("structured_output", "tool")),
            "fallback": str(writing_cfg.get("fallback", "json_text")),
        },
        "thinking_in_response": bool(writing_cfg.get("thinking_may_appear", True)),
        "model_max_tokens_planning": settings.MODEL_MAX_TOKENS_PLANNING,
        "model_max_tokens_reasoning": settings.MODEL_MAX_TOKENS_REASONING,
        "model_max_tokens_writing": settings.MODEL_MAX_TOKENS_WRITING,
        "artifact_chunk_chars": settings.ARTIFACT_CHUNK_CHARS,
        "artifact_max_chunks_per_turn": settings.ARTIFACT_MAX_CHUNKS_PER_TURN,
        "artifact_max_chars_per_turn": settings.ARTIFACT_MAX_CHARS_PER_TURN,
        "artifact_max_write_bytes": settings.ARTIFACT_MAX_WRITE_BYTES,
        "artifact_max_file_bytes": settings.ARTIFACT_MAX_FILE_BYTES,
    }
