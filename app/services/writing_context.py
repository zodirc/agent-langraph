"""Writing-turn context helpers: intent resolution, RAG excerpt injection."""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from app.domain.action import Action

_WRITE_PERSIST_TOOLS = frozenset(
    {"write_text_artifact", "append_text_artifact", "edit_text_artifact"}
)
_WRITING_EXPLICIT_ASK_RE = re.compile(
    r"(请提供|请补充|需要你|需您|需先|用户提供|能否提供|请给出|请告诉我|方可开始)"
)

_WRITING_ACTION_TYPES = frozenset({"write_artifact", "edit_artifact"})
_WRITING_GUIDELINES_TOP_K = 5
_WRITING_GUIDELINES_MAX_CHARS = 6000
_SESSION_SOURCE_TOP_K = 5
_SESSION_SOURCE_MAX_CHARS = 8000


def _hit_domain(hit: Mapping[str, Any]) -> str:
    meta = hit.get("metadata") or {}
    if isinstance(meta, dict):
        domain = meta.get("domain")
        if domain:
            return str(domain).lower()
    return str(hit.get("domain") or "").lower()


def build_writing_guidelines_excerpt(
    state: Mapping[str, Any] | dict[str, Any],
    *,
    top_k: int = _WRITING_GUIDELINES_TOP_K,
    max_chars: int = _WRITING_GUIDELINES_MAX_CHARS,
) -> str:
    """Assemble writing-domain RAG hits into a prompt excerpt."""
    hits = [
        h for h in (state.get("retrieved_knowledge") or []) if isinstance(h, dict)
    ]
    writing_hits = [h for h in hits if _hit_domain(h) == "writing"]
    if not writing_hits:
        return ""

    parts: list[str] = []
    total = 0
    for hit in writing_hits[:top_k]:
        doc_id = str(hit.get("doc_id") or "").strip()
        content = str(hit.get("content") or hit.get("text") or "").strip()
        if not content:
            continue
        header = f"[{doc_id}]" if doc_id else ""
        chunk = f"{header}\n{content}".strip() if header else content
        if total + len(chunk) > max_chars:
            remaining = max_chars - total
            if remaining > 200:
                parts.append(chunk[:remaining] + "\n...(truncated)")
            break
        parts.append(chunk)
        total += len(chunk)
    return "\n\n---\n\n".join(parts)


def build_story_bible_excerpt(task_id: str) -> tuple[str, int, str | None]:
    """Read 素材卡.md deterministically (not via RAG)."""
    from app.services.story_bible import read_story_bible

    return read_story_bible(task_id)


def format_material_usage_line(
    *,
    bible_chars: int,
    rag_segment_count: int,
    warning: str | None = None,
) -> str:
    parts = [f"素材卡 {bible_chars:,} 字"]
    if rag_segment_count:
        parts.append(f"原文检索 {rag_segment_count} 段")
    line = "本轮素材使用：" + " + ".join(parts)
    if warning:
        return f"{warning}\n{line}"
    return line


def build_session_source_excerpt(
    state: Mapping[str, Any] | dict[str, Any],
    *,
    top_k: int = _SESSION_SOURCE_TOP_K,
    max_chars: int = _SESSION_SOURCE_MAX_CHARS,
) -> str:
    """Assemble session source-domain RAG hits into a prompt excerpt."""
    hits = [
        h for h in (state.get("retrieved_knowledge") or []) if isinstance(h, dict)
    ]
    source_hits = [h for h in hits if _hit_domain(h) == "source"]
    if not source_hits:
        return ""

    parts: list[str] = []
    total = 0
    for hit in source_hits[:top_k]:
        doc_id = str(hit.get("doc_id") or "").strip()
        content = str(hit.get("content") or hit.get("text") or "").strip()
        if not content:
            continue
        header = f"[{doc_id}]" if doc_id else ""
        chunk = f"{header}\n{content}".strip() if header else content
        if total + len(chunk) > max_chars:
            remaining = max_chars - total
            if remaining > 200:
                parts.append(chunk[:remaining] + "\n...(truncated)")
            break
        parts.append(chunk)
        total += len(chunk)
    return "\n\n---\n\n".join(parts)


def applied_session_source_ids(
    state: Mapping[str, Any] | dict[str, Any],
    *,
    top_k: int = _SESSION_SOURCE_TOP_K,
) -> list[str]:
    """Doc ids of session source material injected for attribution."""
    hits = [
        h for h in (state.get("retrieved_knowledge") or []) if isinstance(h, dict)
    ]
    ids: list[str] = []
    for hit in hits:
        if _hit_domain(hit) != "source":
            continue
        doc_id = str(hit.get("doc_id") or "").strip()
        if doc_id and doc_id not in ids:
            ids.append(doc_id)
        if len(ids) >= top_k:
            break
    return ids


def applied_writing_guideline_ids(
    state: Mapping[str, Any] | dict[str, Any],
    *,
    top_k: int = _WRITING_GUIDELINES_TOP_K,
) -> list[str]:
    """Doc ids of writing guidelines injected for attribution."""
    hits = [
        h for h in (state.get("retrieved_knowledge") or []) if isinstance(h, dict)
    ]
    ids: list[str] = []
    for hit in hits:
        if _hit_domain(hit) != "writing":
            continue
        doc_id = str(hit.get("doc_id") or "").strip()
        if doc_id and doc_id not in ids:
            ids.append(doc_id)
        if len(ids) >= top_k:
            break
    return ids


def _actions_imply_writing(actions: Sequence[Action]) -> bool:
    return any(a.type in _WRITING_ACTION_TYPES for a in actions)


def _payload_implies_manuscript(payload: Mapping[str, Any]) -> bool:
    mode = str(payload.get("target_mode") or payload.get("current_mode") or "").lower()
    if mode == "manuscript_mode":
        return True
    audit = payload.get("route_audit") or {}
    return str(audit.get("inferred_kind") or "").lower() == "manuscript"


def resolve_writing_intent_for_plan(
    *,
    payload: Mapping[str, Any],
    actions: Sequence[Action],
    goal: str,
) -> dict[str, Any]:
    """Infer writing_intent for unified-actions planning (replaces hard-disabled default)."""
    if _actions_imply_writing(actions):
        return {"enabled": True, "source": "unified_actions"}
    if _payload_implies_manuscript(payload):
        return {"enabled": True, "source": "manuscript_mode"}
    from app.services.artifact_edit_intent import is_artifact_edit_goal

    if is_artifact_edit_goal(goal):
        return {"enabled": True, "source": "unified_actions_edit_goal"}
    return {"enabled": False, "source": "unified_actions"}


def should_force_writing_retrieval(payload: Mapping[str, Any]) -> bool:
    """Writing turns should always run writing+common RAG unless explicitly opted out."""
    intent = payload.get("writing_intent") or {}
    return bool(intent.get("enabled"))


def retrieved_evidence_domains(state: Mapping[str, Any] | dict[str, Any]) -> set[str]:
    domains: set[str] = set()
    for hit in state.get("retrieved_knowledge") or []:
        if isinstance(hit, dict):
            domain = _hit_domain(hit)
            if domain:
                domains.add(domain)
    return domains


def writing_style_only_evidence(state: Mapping[str, Any] | dict[str, Any]) -> bool:
    """True when RAG only recalled writing/common style docs (not plot/fact QA)."""
    domains = retrieved_evidence_domains(state)
    if not domains:
        return False
    return domains <= {"writing", "common"}


def turn_has_persisted_write(tool_results: list[dict[str, Any]]) -> bool:
    """True when a write/append succeeded, or edit applied at least one replacement."""
    from app.domain.action import is_edit_applied

    for item in tool_results:
        tool = str(item.get("tool") or "")
        status = str(item.get("status") or "ok")
        if status not in ("ok", "cached"):
            continue
        if tool in ("write_text_artifact", "append_text_artifact"):
            return True
        if tool == "edit_text_artifact" and is_edit_applied(item.get("result") or {}):
            return True
    return False


def writing_explicit_ask(answer_text: str) -> bool:
    """True when the model is explicitly asking the user for input (allowed terminal)."""
    text = (answer_text or "").strip()
    if not text:
        return False
    from app.services.writing_pending import writing_false_promise_without_write

    if writing_false_promise_without_write(text):
        return False
    return bool(_WRITING_EXPLICIT_ASK_RE.search(text))


def writing_intent_active(state: Mapping[str, Any] | dict[str, Any]) -> bool:
    payload = state.get("input_payload") or {}
    if not isinstance(payload, dict):
        return False
    intent = payload.get("writing_intent") or {}
    return bool(intent.get("enabled"))


def read_loop_should_force_write(
    state: Mapping[str, Any] | dict[str, Any],
    tool_results: list[dict[str, Any]],
) -> bool:
    """True when a writing turn read enough but still owes a write side effect."""
    if not writing_intent_active(state):
        return False
    payload = state.get("input_payload") or {}
    if not isinstance(payload, dict):
        return False
    for item in tool_results:
        tool = str(item.get("tool") or "")
        status = str(item.get("status") or "ok")
        if tool in _WRITE_PERSIST_TOOLS and status in ("ok", "cached"):
            return False
    if str(payload.get("target_mode") or "") == "manuscript_mode":
        return True
    if str(payload.get("thin_execution_profile") or "") == "artifact_edit":
        return True
    return _payload_implies_manuscript(payload)
