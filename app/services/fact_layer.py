"""
Unified turn facts — single source of truth for what executed this session turn.

Reasoning MUST treat `turn_facts` as ground truth for actions already taken.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.runtime.state import AgentState


def _tool_outcome_line(item: dict[str, Any]) -> dict[str, Any]:
    name = str(item.get("tool") or "unknown")
    status = str(item.get("status") or "ok")
    result = item.get("result") if isinstance(item.get("result"), dict) else {}
    line: dict[str, Any] = {
        "tool": name,
        "status": status,
    }
    if result.get("result") is not None:
        line["output"] = result.get("result")
    if result.get("expression"):
        line["expression"] = result.get("expression")
    if result.get("path"):
        line["path"] = result.get("path")
    if result.get("bytes") is not None:
        line["bytes"] = result.get("bytes")
    if result.get("appended_bytes") is not None:
        line["appended_bytes"] = result.get("appended_bytes")
    if result.get("total_bytes") is not None:
        line["total_bytes"] = result.get("total_bytes")
    if result.get("mode"):
        line["mode"] = result.get("mode")
    if result.get("model_name"):
        line["model_name"] = result.get("model_name")
    if item.get("error"):
        line["error"] = item.get("error")
    if result.get("status") == "ok" and not line.get("output") and not line.get("path"):
        line["output_preview"] = str(result)[:300]
    return line


def build_turn_facts(state: AgentState) -> dict[str, Any]:
    """Compile executed nodes, tools, writing, and manuscript into read-only facts."""
    payload = state.get("input_payload") or {}
    tool_lines = [_tool_outcome_line(item) for item in (state.get("tool_results") or [])]
    writing_intent = payload.get("writing_intent") or {}
    manuscript = state.get("manuscript") or {}

    nodes_executed: list[dict[str, Any]] = []
    for entry in state.get("node_history") or []:
        nodes_executed.append(
            {
                "node": entry.get("node"),
                "status": entry.get("status"),
                "at": entry.get("at"),
            }
        )

    executed_actions: list[str] = []
    for line in tool_lines:
        if line.get("status") in ("ok", "success") or line.get("output") or line.get("path"):
            executed_actions.append(f"tool:{line['tool']}")
    if state.get("status") in ("WRITTEN", "TOOL_EXECUTED") and writing_intent.get("enabled"):
        executed_actions.append(f"writing:{writing_intent.get('action', 'write')}")
    if manuscript.get("body_path") and manuscript.get("body_bytes"):
        executed_actions.append(
            f"artifact:{manuscript['body_path']}:{manuscript['body_bytes']}B"
        )

    return {
        "turn": int(state.get("session_turn") or 1),
        "task_id": state["task_id"],
        "session_id": state.get("session_id") or state["task_id"],
        "goal": str(payload.get("goal") or ""),
        "plan": list(state.get("plan") or []),
        "plan_status": state.get("status"),
        "nodes_executed": nodes_executed,
        "tools_executed": tool_lines,
        "writing_intent": writing_intent if writing_intent.get("enabled") else None,
        "manuscript": manuscript or None,
        "progress_metrics": (state.get("progress") or {}).get("metrics"),
        "executed_actions": executed_actions,
        "tool_count": len(tool_lines),
        "has_failures": any(
            line.get("status") in ("error", "skipped") or line.get("error")
            for line in tool_lines
        ),
        "built_at": datetime.now(timezone.utc).isoformat(),
    }


def attach_turn_facts(state: AgentState) -> AgentState:
    """Backward-compatible alias; delegates to observation layer when possible."""
    from app.services.observation import attach_observation

    if state.get("mission"):
        return attach_observation(state)
    from app.runtime.state import merge_state

    facts = build_turn_facts(state)
    payload = dict(state.get("input_payload") or {})
    payload["turn_facts"] = facts
    return merge_state(state, turn_facts=facts, input_payload=payload)


def reasoning_context_from_state(state: AgentState) -> dict[str, Any]:
    """
    Build reasoning LLM context — execution truth from turn_facts only.
    """
    from app.services.runtime_capabilities import (
        build_runtime_capabilities,
        reasoning_instructions_for_state,
    )

    from app.services.conversation_context import (
        build_session_outcomes_digest,
        conversation_history_for_llm,
        conversation_history_from_state,
    )

    payload = state.get("input_payload") or {}
    facts = state.get("turn_facts") or build_turn_facts(state)
    memory_hits = state.get("memory_hits") or []
    raw_history = conversation_history_from_state(state)
    llm_history = conversation_history_for_llm(raw_history)
    outcomes_digest = build_session_outcomes_digest(state)

    return {
        "goal": payload.get("goal"),
        "session_turn": state.get("session_turn"),
        "conversation_history": llm_history,
        "session_outcomes_digest": outcomes_digest,
        "turn_facts": facts,
        "retrieved_knowledge": state.get("retrieved_knowledge") or [],
        "memory_hits": memory_hits[:5],
        "runtime_capabilities": build_runtime_capabilities(),
        "writing_intent": payload.get("writing_intent"),
        "mission": state.get("mission") or payload.get("mission"),
        "instructions": reasoning_instructions_for_state(state),
    }


def validate_reasoning_summary(
    summary: str,
    turn_facts: dict[str, Any],
) -> list[str]:
    """Return warnings when summary claims actions absent from turn_facts."""
    warnings: list[str] = []
    text = (summary or "").lower()
    actions = turn_facts.get("executed_actions") or []
    tools = turn_facts.get("tools_executed") or []

    future_markers = (
        "将要",
        "即将",
        "准备写",
        "开始写",
        "分多次追加",
        "本轮创作",
        "将追加",
        "will write",
        "going to write",
    )
    has_writing = any("writing:" in a for a in actions)
    has_append = any(
        t.get("tool") == "append_text_artifact" or t.get("mode") == "append" for t in tools
    )

    if not has_writing and not has_append:
        for marker in future_markers:
            if marker in text:
                warnings.append(f"summary implies future writing but turn_facts has none: {marker}")
                break

    if "万字" in text or "1.2万" in text:
        total_bytes = sum(
            int(t.get("appended_bytes") or t.get("bytes") or 0) for t in tools
        )
        body_bytes = int((turn_facts.get("manuscript") or {}).get("body_bytes") or 0)
        if total_bytes < 500 and body_bytes < 1000 and ("已写" in summary or "写入" in summary):
            warnings.append("summary claims substantial write but turn_facts bytes are small")

    manuscript = turn_facts.get("manuscript") or {}
    progress_metrics = turn_facts.get("progress_metrics") or {}
    body_bytes = int(manuscript.get("body_bytes") or 0)
    written_chars = int(progress_metrics.get("written_chars") or 0)
    if body_bytes > 1000 and written_chars > 0:
        drift = abs(body_bytes - written_chars) / max(body_bytes, 1)
        if drift > 0.35 and ("进度" in summary or "完成" in summary or "%" in summary):
            warnings.append(
                f"progress_metrics written_chars ({written_chars}) diverges from body_bytes ({body_bytes})"
            )

    intent = turn_facts.get("writing_intent") or {}
    intent_ch = int(intent.get("chapter_index") or 0)
    cursor = int(manuscript.get("chapter_cursor") or 0)
    last_ch = int(manuscript.get("last_chapter_index") or 0)
    if intent_ch > 0 and cursor > 0 and abs(intent_ch - cursor) > 1:
        warnings.append(
            f"chapter_index mismatch: writing_intent={intent_ch} manuscript.cursor={cursor}"
        )
    if intent_ch > 0 and last_ch > 0 and intent_ch < last_ch:
        warnings.append(
            f"writing_intent chapter {intent_ch} behind last_chapter_index {last_ch}"
        )

    import re

    claimed_chapters = re.findall(r"(\d+)\s*章", summary)
    if claimed_chapters and last_ch > 0:
        try:
            claimed_max = max(int(x) for x in claimed_chapters)
            if claimed_max > last_ch + 1:
                warnings.append(
                    f"summary claims chapter {claimed_max} but last_chapter_index is {last_ch}"
                )
        except ValueError:
            pass

    return warnings


def apply_reasoning_guard(
    reasoning_result: dict[str, Any],
    turn_facts: dict[str, Any],
) -> dict[str, Any]:
    """Attach validation warnings; optionally prepend factual lead from turn_facts."""
    summary = str(reasoning_result.get("summary") or "")
    warnings = validate_reasoning_summary(summary, turn_facts)
    structured = dict(reasoning_result.get("structured") or {})
    structured["fact_warnings"] = warnings
    structured["turn_facts_digest"] = {
        "executed_actions": turn_facts.get("executed_actions"),
        "tool_count": turn_facts.get("tool_count"),
    }
    retrieved = reasoning_result.get("retrieved_knowledge") or turn_facts.get("retrieved_knowledge") or []
    if isinstance(retrieved, list) and summary:
        from app.services.rag_eval import citation_coverage, extract_citations

        cited = extract_citations(summary)
        doc_ids = [str(d.get("doc_id")) for d in retrieved if isinstance(d, dict) and d.get("doc_id")]
        unused = [d for d in doc_ids if d not in cited]
        structured["citation_check"] = {
            "cited_doc_ids": cited,
            "unused_doc_ids": unused,
            "coverage": citation_coverage(summary, retrieved) if isinstance(retrieved, list) else 0.0,
        }

    if warnings and turn_facts.get("tools_executed"):
        lead_parts: list[str] = []
        for t in turn_facts["tools_executed"][:4]:
            if t.get("output") is not None:
                lead_parts.append(f"{t['tool']}={t['output']}")
            elif t.get("path"):
                lead_parts.append(f"{t['tool']}→{t['path']}")
        if lead_parts:
            prefix = "【本轮已执行】" + "；".join(lead_parts) + "。"
            if not summary.startswith("【本轮已执行】"):
                summary = prefix + " " + summary

    return {
        **reasoning_result,
        "summary": summary,
        "structured": structured,
    }
