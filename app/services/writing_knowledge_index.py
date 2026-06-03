"""Index writing chapter facts into KnowledgeStore (ADR-001 §6.3 / §12.7)."""

from __future__ import annotations

from typing import Any, Optional

from app.config.settings import settings
from app.domain.writing_memory_models import ChapterOutcome


def chapter_facts_doc_id(task_id: str, chapter_index: int) -> str:
    return f"{task_id}__chapter_facts__ch{int(chapter_index)}"


def upsert_chapter_facts_for_outcome(
    task_id: str,
    outcome: ChapterOutcome,
    *,
    extra: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """
    Persist structured chapter summary into writing RAG as ``chapter_facts`` layer.

    Manuscript body is not indexed wholesale; only extracted outcomes (ADR §6.6).
    """
    if not getattr(settings, "RAG_CHUNK_ENABLED", True):
        return None
    ch = int(outcome.chapter_index)
    if ch < 1:
        return None

    events_text = "\n".join(
        f"- {e.event_type}: {e.subject} — {e.detail}" for e in (outcome.events or [])[:12]
    )
    rubric = outcome.quality_rubric.to_dict() if outcome.quality_rubric else {}
    content = "\n".join(
        [
            f"# 第{ch}章 事实卡",
            f"summary: {outcome.chapter_summary}",
            f"ending_state: {outcome.ending_state}",
            f"hook_for_next: {outcome.hook_for_next}",
            f"events:\n{events_text or '- (none)'}",
            f"quality_rubric: {rubric}",
        ]
    ).strip()

    from app.services.knowledge_store import get_knowledge_store

    metadata = {
        "domain": "writing",
        "layer": "chapter_facts",
        "task_id": task_id,
        "chapter_index": ch,
        "skip_chunking": True,
    }
    if extra:
        metadata.update(extra)

    return get_knowledge_store().upsert_document(
        title=f"chapter_facts ch{ch} ({task_id})",
        content=content,
        metadata=metadata,
        doc_id=chapter_facts_doc_id(task_id, ch),
    )


def load_chapter_facts_from_store(
    task_id: str,
    chapter_index: int,
) -> Optional[str]:
    """Read indexed chapter facts snippet (for tests / debug)."""
    from app.services.knowledge_store import get_knowledge_store

    doc_id = chapter_facts_doc_id(task_id, chapter_index)
    row = get_knowledge_store().get_document(doc_id)
    if not row:
        return None
    return str(row.get("content") or "")
