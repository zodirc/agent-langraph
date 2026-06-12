"""Grounding policy: tool turns use observation; pure QA uses RAG evidence."""

from __future__ import annotations

import json
from typing import Any, Literal

from app.config.settings import settings
from app.runtime.evidence_models import AnswerMode
from app.services.retrieval_policy import has_injected_evidence

GroundingMode = Literal["tool_observation", "rag", "skip"]

_FACTUAL_ANSWER_MODES = frozenset(
    {
        AnswerMode.STRICT_GROUNDED.value,
        AnswerMode.REFUSE_IF_INSUFFICIENT.value,
    }
)

_TRIVIAL_TOOL_NAMES = frozenset({"get_runtime_info", "echo"})
_TOOL_OBSERVATION_KEYS = (
    "content",
    "path",
    "filename",
    "preview",
    "diff",
    "diff_preview",
    "replacements",
    "tail_excerpt",
    "bytes",
    "total_chars",
    "line_count",
    "new_text",
    "old_text",
    "summary",
    "output",
    "result",
    "expression",
)


def _citation_checks_disabled() -> bool:
    strictness = str(
        getattr(settings, "RETRIEVAL_CITATION_CHECK_STRICTNESS", "basic")
    ).lower()
    return strictness == "off" and not settings.RAG_FAITHFULNESS_CHECK_ENABLED


def substantive_tool_results(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Tool results that can ground a user-facing summary (exclude trivial probes)."""
    rows: list[dict[str, Any]] = []
    for item in state.get("tool_results") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("tool") or "")
        if not name or name in _TRIVIAL_TOOL_NAMES:
            continue
        status = str(item.get("status") or "ok").lower()
        if status not in ("ok", "success"):
            continue
        rows.append(item)
    return rows


def _tool_result_observation_text(item: dict[str, Any], *, max_chars: int = 12000) -> str:
    tool = str(item.get("tool") or "tool")
    result = item.get("result")
    parts: list[str] = [f"tool={tool}"]
    if isinstance(result, dict):
        for key in _TOOL_OBSERVATION_KEYS:
            val = result.get(key)
            if val is None or val == "":
                continue
            parts.append(f"{key}={val}")
        if len(parts) == 1:
            parts.append(json.dumps(result, ensure_ascii=False)[:max_chars])
    elif result is not None:
        parts.append(str(result)[:max_chars])
    if item.get("error"):
        parts.append(f"error={item['error']}")
    return "\n".join(parts)[:max_chars]


def tool_observation_hits(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Shape tool_results into retrieval-like hits for grounding_check."""
    hits: list[dict[str, Any]] = []
    for idx, item in enumerate(substantive_tool_results(state)):
        text = _tool_result_observation_text(item)
        if not text.strip():
            continue
        tool = str(item.get("tool") or "tool")
        hits.append(
            {
                "doc_id": f"tool_observation:{tool}:{idx}",
                "content": text,
                "metadata": {"source": "tool_observation", "tool": tool},
            }
        )
    return hits


_SIDE_EFFECT_TOOLS = frozenset(
    {"write_text_artifact", "edit_text_artifact", "append_text_artifact"}
)


def side_effects_verified(state: dict[str, Any]) -> bool:
    """True when this turn's side effects are executor-verified (edit honesty).

    Used by the output guard to decide whether an unfaithful-summary signal can
    be demoted to a warning: when the writes/edits demonstrably happened, the
    summary narrating them is not a fabrication risk even if token overlap with
    evidence is low (CJK narrative rarely matches key=value observation text).
    """
    turn_facts = state.get("turn_facts") or {}
    if turn_facts.get("edit_applied") is False:
        return False
    for row in state.get("tool_results") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("tool") or "") not in _SIDE_EFFECT_TOOLS:
            continue
        if str(row.get("status") or "ok").lower() not in ("ok", "success"):
            return False
    return True


def is_factual_answer_mode(answer_mode: str) -> bool:
    """True when the turn contract expects evidence-backed factual claims."""
    return str(answer_mode or "") in _FACTUAL_ANSWER_MODES


def writing_turn_skips_rag_grounding(state: dict[str, Any], *, answer_mode: str) -> bool:
    """
    Writing turns without persisted writes and non-factual answer_mode do not
    need lexical faithfulness against retrieved style/source material.
    """
    from app.services.writing_context import turn_has_persisted_write, writing_intent_active

    if not writing_intent_active(state):
        payload = state.get("input_payload") or {}
        if str(payload.get("target_mode") or "") != "manuscript_mode":
            return False
    if turn_has_persisted_write(state.get("tool_results") or []):
        return False
    return not is_factual_answer_mode(answer_mode)


def classify_grounding_turn(state: dict[str, Any]) -> GroundingMode:
    """
    tool_observation — summary should align with tool_results / side effects.
    rag — pure QA; summary should align with retrieved knowledge / memory.
    skip — no evidence to ground against.
    """
    if _citation_checks_disabled():
        return "skip"
    if tool_observation_hits(state):
        return "tool_observation"
    payload = state.get("input_payload") or {}
    contract = payload.get("turn_contract") or {}
    primary = str(contract.get("primary_op") or "") if isinstance(contract, dict) else ""
    if primary in ("edit_plot", "review_outline", "run_tools", "batch_unit_quality"):
        return "tool_observation"
    if state.get("skip_retrieval") and not has_injected_evidence(state):
        return "skip"
    if has_injected_evidence(state):
        from app.services.thin_execution import thin_execution_profile
        from app.services.writing_context import (
            writing_intent_active,
            writing_style_only_evidence,
        )

        reasoning = state.get("reasoning_result") or {}
        structured = reasoning.get("structured") if isinstance(reasoning.get("structured"), dict) else {}
        if (
            thin_execution_profile(payload) == "session_source_qa"
            and isinstance(structured, dict)
            and structured.get("source") == "session_source_fast"
        ):
            return "skip"

        from app.services.evidence_pipeline import get_answer_mode

        answer_mode = get_answer_mode(state)
        if writing_turn_skips_rag_grounding(state, answer_mode=answer_mode):
            return "skip"
        # Legacy weak signal: style-only RAG corpus (kept as secondary escape).
        if writing_intent_active(state) and writing_style_only_evidence(state):
            return "skip"
        return "rag"
    return "skip"


def should_run_grounding_check(state: dict[str, Any]) -> bool:
    """Run faithfulness when we have tool observation or RAG evidence to check against."""
    return classify_grounding_turn(state) != "skip"


def grounding_hits_for_state(state: dict[str, Any]) -> tuple[GroundingMode, list[dict[str, Any]]]:
    """Evidence corpus for output_guard grounding."""
    mode = classify_grounding_turn(state)
    if mode == "tool_observation":
        hits = tool_observation_hits(state)
        if hits:
            return mode, hits
        return mode, []
    if mode == "rag":
        return mode, list(state.get("retrieved_knowledge") or [])
    return "skip", []
