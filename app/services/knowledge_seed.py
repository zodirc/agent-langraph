from __future__ import annotations

import logging
from pathlib import Path

from app.services.knowledge_paths import (
    code_editing_guidelines_path,
    prose_voice_format_path,
    writing_guidelines_path,
)
from app.services.knowledge_store import get_knowledge_store

logger = logging.getLogger(__name__)

_WRITING_GUIDELINES_PATH = writing_guidelines_path()
_PROSE_VOICE_FORMAT_PATH = prose_voice_format_path()
_CODE_EDITING_GUIDELINES_PATH = code_editing_guidelines_path()

DEFAULT_DOCUMENTS = [
    {
        "doc_id": "builtin-architecture",
        "title": "LangGraph Agent Runtime Overview",
        "content": (
            "This project uses LangGraph as the execution runtime with nodes for planning, "
            "retrieval, tool execution, reasoning, policy checks, human review, output, "
            "and memory writeback."
        ),
        "metadata": {"source": "builtin", "topic": "architecture", "domain": "common"},
    },
    {
        "doc_id": "builtin-policy",
        "title": "Policy Engine Rules",
        "content": (
            "Policy results include CONTINUE, REVIEW, REJECT, and ESCALATE. "
            "HIGH risk requires human review. CRITICAL risk is auto rejected."
        ),
        "metadata": {"source": "builtin", "topic": "policy", "domain": "common"},
    },
    {
        "doc_id": "builtin-retrieval",
        "title": "Knowledge Retrieval Strategy",
        "content": (
            "Knowledge retrieval uses hybrid search combining ChromaDB vector similarity "
            "and SQLite keyword matching merged with reciprocal rank fusion."
        ),
        "metadata": {"source": "builtin", "topic": "retrieval", "domain": "common"},
    },
    {
        "doc_id": "builtin-writing-guidelines",
        "title": "长文写作规范",
        "content_path": _WRITING_GUIDELINES_PATH,
        "metadata": {"source": "builtin", "topic": "writing", "domain": "writing", "locale": "zh-CN"},
    },
    {
        "doc_id": "builtin-prose-voice-format",
        "title": "写作口吻与TXT排版规范",
        "content_path": _PROSE_VOICE_FORMAT_PATH,
        "metadata": {
            "source": "builtin",
            "topic": "writing",
            "domain": "writing",
            "locale": "zh-CN",
            "focus": "prose_voice,anti_ai,txt_format,layout",
        },
    },
    {
        "doc_id": "builtin-code-editing-guidelines",
        "title": "代码编辑与修复指南",
        "content_path": _CODE_EDITING_GUIDELINES_PATH,
        "metadata": {
            "source": "builtin",
            "topic": "code",
            "domain": "code",
            "locale": "zh-CN",
            "focus": "safe_edits,tests,diff_review",
        },
    },
]


def _load_content(spec: dict) -> str:
    path = spec.get("content_path")
    if path:
        file_path = Path(path)
        if file_path.is_file():
            return file_path.read_text(encoding="utf-8")
        logger.warning("Builtin knowledge file missing: %s", file_path)
        return str(spec.get("content") or "")
    return str(spec.get("content") or "")


def seed_default_knowledge() -> int:
    """Seed empty stores on first boot (legacy behavior)."""
    store = get_knowledge_store()
    if store.count() > 0:
        return 0
    return ensure_builtin_knowledge()


def ensure_builtin_knowledge() -> int:
    """Upsert fixed builtin doc_ids (safe on every startup; refreshes writing guidelines from disk)."""
    store = get_knowledge_store()
    updated = 0
    for doc in DEFAULT_DOCUMENTS:
        content = _load_content(doc)
        if not content.strip():
            continue
        store.upsert_document(
            title=doc["title"],
            content=content,
            metadata=doc["metadata"],
            doc_id=doc["doc_id"],
        )
        updated += 1
    if updated:
        logger.info("Ensured %s builtin knowledge document(s)", updated, extra={"seeded": updated})
    return updated
