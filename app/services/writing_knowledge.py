"""Writing-task knowledge: retrieval query enrichment and guidelines for writing_context."""

from __future__ import annotations

from typing import Any, Optional

from app.runtime.state import AgentState
from app.services.knowledge_paths import prose_voice_format_path, writing_guidelines_path

WRITING_GUIDELINES_DOC_ID = "builtin-writing-guidelines"
PROSE_VOICE_FORMAT_DOC_ID = "builtin-prose-voice-format"
WRITING_KNOWLEDGE_DOC_IDS = (WRITING_GUIDELINES_DOC_ID, PROSE_VOICE_FORMAT_DOC_ID)

_WRITING_RAG_QUERY_SUFFIX = (
    "写作规范 口吻 去AI化 自然叙事 剧情连贯 伏笔 衔接 TXT排版 段落 章题 全角标点"
)
_REPO_GUIDELINES = writing_guidelines_path()
_REPO_PROSE_FORMAT = prose_voice_format_path()
_MAX_EXCERPT_CHARS = 5200

_TITLE_MARKERS = ("写作规范", "口吻", "排版", "TXT", "长文写作")


def writing_intent_active(state: AgentState | dict) -> bool:
    payload = state.get("input_payload") or {}
    intent = payload.get("writing_intent") or {}
    return bool(intent.get("enabled"))


def enrich_retrieval_query_for_writing(state: AgentState | dict, query: str) -> str:
    """Bias hybrid RAG toward writing-guidelines docs on fiction writing turns."""
    if not writing_intent_active(state):
        return query
    base = (query or "").strip()
    if "去AI化" in base and "TXT排版" in base:
        return base[:2000]
    combined = f"{base} {_WRITING_RAG_QUERY_SUFFIX}".strip()
    return combined[:2000]


def _truncate_excerpt(text: str, max_chars: int = _MAX_EXCERPT_CHARS) -> str:
    body = (text or "").strip()
    if len(body) <= max_chars:
        return body
    return body[: max_chars - 20].rstrip() + "\n...(truncated)"


def _is_writing_knowledge_hit(hit: dict[str, Any]) -> bool:
    doc_id = str(hit.get("doc_id") or "")
    meta = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    parent_id = str(meta.get("parent_doc_id") or "")
    if doc_id in WRITING_KNOWLEDGE_DOC_IDS or parent_id in WRITING_KNOWLEDGE_DOC_IDS:
        return True
    title = str(hit.get("title") or "")
    return any(marker in title for marker in _TITLE_MARKERS)


def _merge_excerpt_parts(parts: list[str]) -> Optional[str]:
    cleaned = [p.strip() for p in parts if (p or "").strip()]
    if not cleaned:
        return None
    return _truncate_excerpt("\n\n---\n\n".join(cleaned))


def _excerpt_from_retrieved(state: AgentState | dict) -> Optional[str]:
    parts: list[str] = []
    seen: set[str] = set()
    for hit in state.get("retrieved_knowledge") or []:
        if not isinstance(hit, dict) or not _is_writing_knowledge_hit(hit):
            continue
        meta = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
        group_id = str(meta.get("parent_doc_id") or hit.get("doc_id") or hit.get("title") or "")
        if group_id in seen:
            continue
        seen.add(group_id)
        content = str(hit.get("content") or "").strip()
        if content:
            parts.append(content)
    return _merge_excerpt_parts(parts)


def _load_repo_excerpts() -> list[str]:
    parts: list[str] = []
    for path in (_REPO_GUIDELINES, _REPO_PROSE_FORMAT):
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    parts.append(text)
            except OSError:
                continue
    return parts


def _excerpt_from_knowledge_store(query_hint: str) -> Optional[str]:
    try:
        from app.services.knowledge_store import get_knowledge_store

        store = get_knowledge_store()
        parts: list[str] = []
        for doc_id in WRITING_KNOWLEDGE_DOC_IDS:
            doc = store.get_document(doc_id)
            if doc and str(doc.get("content") or "").strip():
                parts.append(str(doc["content"]))
        if len(parts) >= len(WRITING_KNOWLEDGE_DOC_IDS):
            return _merge_excerpt_parts(parts)

        query = enrich_retrieval_query_for_writing(
            {"input_payload": {"writing_intent": {"enabled": True}}},
            query_hint or "写作规范 口吻 TXT排版",
        )
        seen: set[str] = set()
        for hit in store.hybrid_search(query, top_k=8):
            doc_id = str(hit.get("doc_id") or "")
            if doc_id in seen:
                continue
            if not _is_writing_knowledge_hit(hit):
                continue
            seen.add(doc_id)
            content = str(hit.get("content") or "").strip()
            if content:
                parts.append(content)
        if parts:
            return _merge_excerpt_parts(parts)
    except Exception:
        return None
    return None


def resolve_writing_guidelines_excerpt(
    state: AgentState | dict,
    *,
    query_hint: str = "",
) -> Optional[str]:
    """
    Resolve writing guidelines for injection into writing_context.

    Priority: retrieved_knowledge hits → knowledge store docs/search → repo files.
    Merges core guidelines + prose voice / TXT format docs when available.
    """
    from_retrieved = _excerpt_from_retrieved(state)
    if from_retrieved:
        return from_retrieved
    from_repo = _merge_excerpt_parts(_load_repo_excerpts())
    if from_repo:
        return from_repo
    return _excerpt_from_knowledge_store(query_hint)
