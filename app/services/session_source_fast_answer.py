"""Deterministic shortcut for session-source confirmation QA (§7.3 scheme E)."""

from __future__ import annotations

from typing import Any, Optional

from app.config.settings import settings
from app.runtime.state import AgentState
from app.services.thin_execution import thin_execution_profile
from app.services.writing_context import (
    applied_session_source_ids,
    build_session_source_excerpt,
    retrieved_evidence_domains,
)


def session_source_fast_answer_enabled() -> bool:
    return bool(getattr(settings, "SESSION_SOURCE_FAST_ANSWER_ENABLED", True))


def try_session_source_fast_answer(state: AgentState | dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Build reasoning_result without LLM when user only asks whether session material was loaded.

    Requires prior retrieval to have populated source-domain hits in state.
    """
    if not session_source_fast_answer_enabled():
        return None
    payload = state.get("input_payload") or {}
    if str(payload.get("force_slow_reasoning") or "").lower() in ("1", "true", "yes"):
        return None
    profile = thin_execution_profile(payload)
    if profile != "session_source_qa":
        return None

    domains = retrieved_evidence_domains(state)
    source_ids = applied_session_source_ids(state)
    excerpt = build_session_source_excerpt(state)

    if source_ids or excerpt or "source" in domains:
        titles = _source_titles(state, source_ids)
        title_part = "、".join(titles[:3]) if titles else "本会话素材"
        if len(titles) > 3:
            title_part += f" 等 {len(titles)} 篇"
        summary = (
            f"是的，我已读取本会话导入的写作素材（{title_part}）。"
            "后续写作与问答会优先引用这些材料，不会凭空编造剧情或设定。"
        )
        return _result(summary, structured={"source": "session_source_fast", "doc_ids": source_ids})

    summary = (
        "当前会话尚未检索到已导入的写作素材。"
        "请使用「导入素材」或 /import 上传剧情、人物、设定等参考文件后再试。"
    )
    return _result(summary, confidence=0.88, structured={"source": "session_source_fast", "doc_ids": []})


def _source_titles(state: AgentState | dict[str, Any], doc_ids: list[str]) -> list[str]:
    id_set = set(doc_ids)
    titles: list[str] = []
    for hit in state.get("retrieved_knowledge") or []:
        if not isinstance(hit, dict):
            continue
        doc_id = str(hit.get("doc_id") or "")
        if doc_ids and doc_id not in id_set:
            continue
        title = str(hit.get("title") or doc_id or "").strip()
        if title and title not in titles:
            titles.append(title)
    return titles


def _result(
    summary: str,
    *,
    confidence: float = 0.95,
    structured: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "summary": summary,
        "confidence": confidence,
        "risk_level": "LOW",
        "structured": {**(structured or {}), "fast_reasoning": True},
        "fast_reasoning": True,
    }
