"""Query construction layer: compile user input into a structured QueryObject."""

from __future__ import annotations

import re
from typing import Any

from app.config.settings import settings
from app.runtime.evidence_models import QueryObject, RetrievalDecision, RetrievalPurpose, TimeScope
from app.runtime.state import AgentState
from app.services.conversation_context import (
    conversation_history_for_llm,
    conversation_history_from_state,
)
from app.services.memory_query import should_suppress_session_memory

_ERROR_CODE_RE = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")
_CLASS_NAME_RE = re.compile(r"\b[A-Z][a-zA-Z0-9_]{2,}\b")
_CONFIG_KEY_RE = re.compile(r"(?i)\b[\w.-]+\.(yaml|yml|json|toml|ini|conf)\b|[\w]+_[\w]+")
_PRONOUN_RE = re.compile(
    r"(?i)\b(it|this|that|those|these|above|previous|earlier|the same)\b|"
    r"(它|这个|那个|上述|之前|同样)"
)
_TIME_LATEST_RE = re.compile(r"(?i)\b(latest|newest|current|recent)\b|最新|当前")
_TIME_HISTORICAL_RE = re.compile(r"(?i)\b(history|historical|legacy|deprecated|old version)\b|历史|旧版")


_SOFT_SYNONYMS: dict[str, list[str]] = {
    "error": ["exception", "failure", "报错"],
    "fix": ["repair", "resolve", "修复"],
    "deploy": ["deployment", "发布", "部署"],
    "compare": ["versus", "difference", "对比"],
    "latest": ["newest", "current", "最新"],
}


def _extract_soft_terms(text: str, must_have: list[str]) -> list[str]:
    lower = text.lower()
    soft: list[str] = []
    for key, syns in _SOFT_SYNONYMS.items():
        if key in lower or any(s in lower for s in syns):
            for s in syns:
                if s not in must_have and s not in soft:
                    soft.append(s)
    return soft[:8]


def _extract_must_have_terms(text: str) -> list[str]:
    terms: list[str] = []
    for pattern in (_ERROR_CODE_RE, _CLASS_NAME_RE, _CONFIG_KEY_RE):
        for match in pattern.findall(text):
            item = match if isinstance(match, str) else str(match)
            if len(item) >= 3 and item not in terms:
                terms.append(item)
    return terms[:12]


def _extract_task_constraints(text: str, purpose: str) -> list[str]:
    constraints: list[str] = []
    lower = text.lower()
    if purpose in ("procedural_howto", "code_fix"):
        constraints.append("needs_steps")
    if purpose == "code_fix":
        constraints.append("needs_code_fix")
    if purpose == "comparative_summary":
        constraints.append("needs_multi_source")
    if purpose == "fact_qa":
        constraints.append("needs_single_evidence")
    if "?" in text or "？" in text:
        constraints.append("is_question")
    if len(lower.split()) <= 6:
        constraints.append("short_query")
    return constraints


def _resolve_time_scope(text: str) -> str:
    if _TIME_LATEST_RE.search(text):
        return TimeScope.LATEST.value
    if _TIME_HISTORICAL_RE.search(text):
        return TimeScope.HISTORICAL.value
    return TimeScope.UNSPECIFIED.value


def _history_constraints(state: AgentState | dict[str, Any]) -> list[str]:
    """Extract whitelisted constraints from prior turns."""
    if should_suppress_session_memory(state):
        return []
    history = conversation_history_for_llm(conversation_history_from_state(state))
    constraints: list[str] = []
    for msg in reversed(history[:-1]):
        if str(msg.get("role")) != "user":
            continue
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        if re.search(r"(?i)\b(format|output|json|table|list)\b|格式|输出", content):
            constraints.append(f"output_format:{content[:80]}")
        entities = _extract_must_have_terms(content)
        for ent in entities[:3]:
            constraints.append(f"entity:{ent}")
        break
    return constraints


def _standalone_rewrite(text: str, state: AgentState | dict[str, Any]) -> str:
    """Resolve pronouns with minimal history context for retrieval."""
    if not getattr(settings, "RETRIEVAL_QUERY_REWRITE_ENABLED", True):
        return text
    if not _PRONOUN_RE.search(text):
        return text

    history = conversation_history_for_llm(conversation_history_from_state(state))
    anchor = ""
    for msg in reversed(history):
        if str(msg.get("role")) == "user":
            anchor = str(msg.get("content") or "").strip()
            if anchor and anchor != text:
                break
    if not anchor:
        return text

    must_terms = _extract_must_have_terms(anchor)
    prefix = " ".join(must_terms[:4])
    rewritten = f"{prefix} {text}".strip() if prefix else f"{anchor[:120]} {text}"
    return rewritten[:2000]


def build_query_object(
    state: AgentState | dict[str, Any],
    decision: RetrievalDecision | None = None,
) -> QueryObject:
    """Build structured query from state and optional retrieval decision."""
    from app.services.retrieval_decision import build_retrieval_decision

    if decision is None:
        decision = build_retrieval_decision(state)

    payload = state.get("input_payload") or {}
    original = str(
        payload.get("goal") or payload.get("query") or payload.get("question") or ""
    ).strip()
    standalone = _standalone_rewrite(original, state) if original else ""
    if not standalone:
        standalone = str(state.get("task_type") or "qa")

    must_have = _extract_must_have_terms(standalone)
    if must_have and must_have[0] not in standalone:
        standalone = f"{' '.join(must_have[:4])} {standalone}".strip()

    from app.services.writing_knowledge import enrich_retrieval_query_for_writing

    standalone = enrich_retrieval_query_for_writing(state, standalone)

    task_constraints = _extract_task_constraints(standalone, decision.purpose)
    task_constraints.extend(_history_constraints(state))

    source_scope: list[str] = []
    if decision.authority_required:
        source_scope.append("official_docs")
    if decision.purpose == RetrievalPurpose.CODE_FIX.value:
        source_scope.append("code")

    soft_terms = _extract_soft_terms(standalone, must_have)

    return QueryObject(
        original_query=original,
        standalone_query=standalone[:2000],
        must_have_terms=must_have,
        soft_terms=soft_terms,
        task_constraints=task_constraints,
        time_scope=_resolve_time_scope(standalone),
        source_scope=source_scope,
        debug_info={"purpose": decision.purpose, "rewritten": standalone != original},
    )
