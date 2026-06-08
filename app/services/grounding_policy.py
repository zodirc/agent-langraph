"""Grounding policy: tool turns use observation; pure QA uses RAG evidence."""

from __future__ import annotations

import json
from typing import Any, Literal

from app.config.settings import settings
from app.services.retrieval_policy import has_injected_evidence

GroundingMode = Literal["tool_observation", "rag", "skip"]

_TRIVIAL_TOOL_NAMES = frozenset({"get_runtime_info", "echo"})
_TOOL_OBSERVATION_KEYS = (
    "content",
    "path",
    "filename",
    "preview",
    "diff",
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
