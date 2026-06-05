"""Task intent layer: structured retrieval decision before search."""

from __future__ import annotations

import re
from typing import Any

from app.config.settings import settings
from app.runtime.evidence_models import AnswerMode, RetrievalDecision, RetrievalPurpose
from app.runtime.state import AgentState
from app.services.retrieval_policy import skip_knowledge_retrieval

_CODE_PATTERNS = (
    re.compile(r"(?i)\b(error|exception|traceback|stack\s*trace|fix|debug|compile)\b"),
    re.compile(r"(?i)\b(api|config|yaml|json|docker|kubernetes|sql)\b"),
    re.compile(r"[A-Z][a-zA-Z]+Error"),
    re.compile(r"\b[A-Z]{2,}_[A-Z0-9_]+\b"),
)
_HOWTO_PATTERNS = (
    re.compile(r"(?i)\b(how\s+to|steps?|procedure|tutorial|guide|walkthrough)\b"),
    re.compile(r"(?i)(怎么|如何|步骤|教程)"),
)
_COMPARE_PATTERNS = (
    re.compile(r"(?i)\b(compare|versus|vs\.?|difference|pros?\s+and\s+cons?)\b"),
    re.compile(r"(?i)(对比|比较|区别|优缺点)"),
)
_LATEST_PATTERNS = (
    re.compile(r"(?i)\b(latest|newest|current|recent|up[\s-]?to[\s-]?date)\b"),
    re.compile(r"(?i)(最新|当前版本|最近)"),
)
_AUTHORITY_PATTERNS = (
    re.compile(r"(?i)\b(official|specification|standard|documentation|规范|官方)\b"),
)


def _user_text(state: AgentState | dict[str, Any]) -> str:
    payload = state.get("input_payload") or {}
    return str(
        payload.get("goal") or payload.get("query") or payload.get("question") or ""
    ).strip()


def _classify_purpose(text: str, state: AgentState | dict[str, Any]) -> str:
    task_type = str(state.get("task_type") or "").lower()
    payload = state.get("input_payload") or {}
    audit = payload.get("route_audit") or {}
    inferred = str(audit.get("inferred_kind") or "").lower()

    if task_type in {"code", "engineering"} or inferred == "code":
        return RetrievalPurpose.CODE_FIX.value
    if any(p.search(text) for p in _CODE_PATTERNS):
        return RetrievalPurpose.CODE_FIX.value
    if any(p.search(text) for p in _HOWTO_PATTERNS):
        return RetrievalPurpose.PROCEDURAL_HOWTO.value
    if any(p.search(text) for p in _COMPARE_PATTERNS):
        return RetrievalPurpose.COMPARATIVE_SUMMARY.value
    if state.get("plan") and skip_knowledge_retrieval(state):
        return RetrievalPurpose.PLANNING_BACKGROUND.value
    if len(text.split()) <= 8 and "?" in text:
        return RetrievalPurpose.FACT_QA.value
    return RetrievalPurpose.GENERAL_GROUNDED.value


def _needs_tools(state: AgentState | dict[str, Any]) -> bool:
    tools = state.get("selected_tools") or []
    if tools:
        return True
    payload = state.get("input_payload") or {}
    contract_tools = payload.get("required_tools") or payload.get("tools") or []
    return bool(contract_tools)


def _answer_mode_for_purpose(purpose: str, *, authority_required: bool) -> str:
    if purpose == RetrievalPurpose.FACT_QA.value:
        return AnswerMode.STRICT_GROUNDED.value
    if purpose == RetrievalPurpose.CODE_FIX.value:
        return AnswerMode.REFUSE_IF_INSUFFICIENT.value
    if authority_required:
        return AnswerMode.STRICT_GROUNDED.value
    return AnswerMode.BEST_EFFORT_GROUNDED.value


def build_retrieval_decision(state: AgentState | dict[str, Any]) -> RetrievalDecision:
    """Produce structured retrieval decision from agent state."""
    text = _user_text(state)
    purpose = _classify_purpose(text, state)
    freshness_required = any(p.search(text) for p in _LATEST_PATTERNS)
    authority_required = any(p.search(text) for p in _AUTHORITY_PATTERNS)
    need_tools = _needs_tools(state)

    skip = skip_knowledge_retrieval(state)
    fast_skip = (
        getattr(settings, "SKIP_RETRIEVAL_WHEN_NO_TOOLS", False)
        and not need_tools
        and not (state.get("plan") or [])
    )
    need_retrieval = not skip and not fast_skip

    skip_reason = ""
    if skip:
        skip_reason = "planning_skip_retrieval"
    elif fast_skip:
        skip_reason = "no_tools_fast_path"

    return RetrievalDecision(
        need_retrieval=need_retrieval,
        need_tools=need_tools,
        purpose=purpose,
        freshness_required=freshness_required,
        authority_required=authority_required,
        answer_mode=_answer_mode_for_purpose(purpose, authority_required=authority_required),
        skip_reason=skip_reason,
        debug_info={"query_len": len(text), "task_type": state.get("task_type")},
    )
