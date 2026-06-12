"""Story bible (素材卡) distillation and deterministic read."""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

STORY_BIBLE_SYSTEM = (
    "将用户上传的小说/改编素材蒸馏为结构化素材卡（Markdown，≤2500 字）。\n"
    "必须包含以下章节（用 ## 标题）：\n"
    "## 改编要求（硬约束）\n"
    "## 主要人物\n"
    "## 情节点（按时间线）\n"
    "## 世界观与设定\n"
    "逐条列出素材中的改编要求，不可遗漏。只输出 Markdown，不要 JSON。"
)

_STORY_BIBLE_HEADER = "# 素材卡（自动生成，可手动编辑）"
_distill_lock = threading.Lock()
_distill_status: dict[str, dict[str, Any]] = {}


def story_bible_relative_path(task_id: str) -> str:
    from app.services.writing_project import DEFAULT_BIBLE, load_project

    project = load_project(task_id)
    return project.bible if project else DEFAULT_BIBLE


def read_story_bible(task_id: str) -> tuple[str, int, str | None]:
    """
    Read 素材卡.md from artifact dir.

    Returns (text, char_count, warning).
    """
    from app.services.artifact_tools import task_artifact_dir

    rel = story_bible_relative_path(task_id)
    path = task_artifact_dir(task_id) / rel
    if not path.is_file():
        return "", 0, None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return "", 0, f"WARN: 素材卡存在但读取失败: {exc}"
    if not text or text == _STORY_BIBLE_HEADER:
        return "", 0, None
    return text, len(text), None


def write_story_bible(task_id: str, content: str) -> int:
    from app.services.artifact_tools import task_artifact_dir
    from app.services.writing_project import DEFAULT_BIBLE, ensure_writing_project

    ensure_writing_project(task_id)
    rel = story_bible_relative_path(task_id) or DEFAULT_BIBLE
    path = task_artifact_dir(task_id) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    body = content.strip()
    if not body.startswith("#"):
        body = f"{_STORY_BIBLE_HEADER}\n\n{body}"
    path.write_text(body, encoding="utf-8")
    return len(body)


def mark_story_bible_distillation_started(task_id: str) -> None:
    with _distill_lock:
        _distill_status[str(task_id)] = {"status": "pending", "chars": 0}


def mark_story_bible_distillation_done(task_id: str, *, chars: int, failed: bool = False) -> None:
    with _distill_lock:
        _distill_status[str(task_id)] = {
            "status": "failed" if failed else "ready",
            "chars": int(chars),
        }


def story_bible_status(task_id: str) -> dict[str, Any]:
    text, chars, warn = read_story_bible(task_id)
    with _distill_lock:
        pending = dict(_distill_status.get(str(task_id)) or {})
    return {
        "task_id": task_id,
        "ready": bool(text),
        "chars": chars if text else int(pending.get("chars") or 0),
        "pending": pending.get("status") == "pending",
        "status": pending.get("status") or ("ready" if text else "absent"),
        "warning": warn,
    }


def clear_story_bible_status_for_tests() -> None:
    with _distill_lock:
        _distill_status.clear()


def distill_story_bible_async(
    task_id: str,
    *,
    title: str,
    content: str,
) -> None:
    """Background distillation after source material upload."""
    if not (content or "").strip():
        return
    mark_story_bible_distillation_started(task_id)
    thread = threading.Thread(
        target=_distill_story_bible,
        kwargs={"task_id": task_id, "title": title, "content": content},
        daemon=True,
        name=f"story-bible-{task_id[:8]}",
    )
    thread.start()


def _distill_story_bible(*, task_id: str, title: str, content: str) -> None:
    from app.config.settings import settings

    if not settings.MODEL_ENABLED:
        chars = _write_fallback_bible(task_id, title, content)
        mark_story_bible_distillation_done(task_id, chars=chars)
        return
    try:
        from app.services.llm_client import get_llm
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = get_llm("summarization")
        if llm is None:
            chars = _write_fallback_bible(task_id, title, content)
            mark_story_bible_distillation_done(task_id, chars=chars)
            return
        user = f"标题: {title}\n\n素材原文:\n{content[:80000]}"
        response = llm.invoke(
            [SystemMessage(content=STORY_BIBLE_SYSTEM), HumanMessage(content=user)]
        )
        raw = response.content if hasattr(response, "content") else str(response)
        text = str(raw).strip()
        if not text:
            chars = _write_fallback_bible(task_id, title, content)
            mark_story_bible_distillation_done(task_id, chars=chars)
            return
        if len(text) > 2800:
            text = text[:2800] + "\n...(truncated)"
        chars = write_story_bible(task_id, text)
        mark_story_bible_distillation_done(task_id, chars=chars)
        logger.info("Story bible distilled for %s (%s chars)", task_id, chars)
    except Exception as exc:
        logger.warning("Story bible distillation failed for %s: %s", task_id, exc)
        chars = _write_fallback_bible(task_id, title, content)
        mark_story_bible_distillation_done(task_id, chars=chars, failed=True)


def _write_fallback_bible(task_id: str, title: str, content: str) -> int:
    excerpt = content[:2000].strip()
    body = (
        f"{_STORY_BIBLE_HEADER}\n\n"
        f"## 改编要求（硬约束）\n"
        f"- 来源: {title}\n\n"
        f"## 素材摘录\n\n{excerpt}"
    )
    return write_story_bible(task_id, body)


def should_supplement_source_rag(source_chars: int) -> bool:
    """RAG supplement only for very long source material (>50k chars)."""
    return source_chars > 50000


def _outline_query_for_rag(task_id: str, state: dict[str, Any] | None = None) -> str:
    from app.services.artifact_tools import task_artifact_dir
    from app.services.writing_project import load_project

    parts: list[str] = []
    project = load_project(task_id)
    if project:
        outline_path = task_artifact_dir(task_id) / project.outline
        if outline_path.is_file():
            parts.append(outline_path.read_text(encoding="utf-8")[:4000])
    if state:
        payload = state.get("input_payload") or {}
        goal = str(payload.get("goal") or payload.get("query") or "").strip()
        if goal:
            parts.append(goal)
    return "\n".join(p for p in parts if p).strip()


def build_source_rag_supplement_excerpt(
    state: dict[str, Any],
    task_id: str,
    *,
    top_k: int = 5,
    max_chars: int = 4000,
) -> tuple[str, int]:
    """
    Supplemental source-domain RAG using outline + goal as query (long corpus only).
    Returns (excerpt, segment_count).
    """
    query = _outline_query_for_rag(task_id, state)
    if not query:
        return "", 0
    session_id = str(state.get("session_id") or task_id)
    try:
        from app.services.knowledge_store import get_knowledge_store
        from app.services.session_scope import set_retrieval_session_id

        store = get_knowledge_store()
        set_retrieval_session_id(session_id)
        try:
            hits = store.hybrid_search(query, top_k=top_k, domains={"source"})
        finally:
            set_retrieval_session_id(None)
    except Exception as exc:
        logger.warning("source RAG supplement failed for %s: %s", task_id, exc)
        return "", 0

    parts: list[str] = []
    total = 0
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        content = str(hit.get("content") or hit.get("text") or "").strip()
        if not content:
            continue
        doc_id = str(hit.get("doc_id") or "").strip()
        chunk = f"[{doc_id}]\n{content}" if doc_id else content
        if total + len(chunk) > max_chars:
            remaining = max_chars - total
            if remaining > 200:
                parts.append(chunk[:remaining] + "\n...(truncated)")
            break
        parts.append(chunk)
        total += len(chunk)
    excerpt = "\n\n---\n\n".join(parts)
    return excerpt, len(parts)
