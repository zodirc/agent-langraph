"""回合事实层
reasoning_node 必须以 turn_facts 为已发生动作的真值

turn_facts: canonical record of tools/RAG/writing executed this turn.
Reasoning must not contradict turn_facts."""

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
    from app.services.turn_event_log import get_turn_event_log

    event_log = get_turn_event_log(state)
    if event_log.events:
        base = _build_turn_facts_snapshot(state)
        return {
            **base,
            "events": event_log.events_as_dicts(),
            "execution_facts": event_log.execution_facts(),
            "decision_facts": event_log.decision_facts(),
            "quality_facts": event_log.quality_facts(),
            "event_count": len(event_log.events),
            "has_failures": bool(event_log.failure_events()) or bool(base.get("has_failures")),
        }
    return _build_turn_facts_snapshot(state)


def _build_turn_facts_snapshot(state: AgentState) -> dict[str, Any]:
    payload = state.get("input_payload") or {}
    tool_lines = [_tool_outcome_line(item) for item in (state.get("tool_results") or [])]
    writing_intent = payload.get("writing_intent") or {}

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
        if line.get("tool") in ("write_text_artifact", "append_text_artifact", "edit_text_artifact") and line.get(
            "status"
        ) in ("ok", "success"):
            executed_actions.append(f"writing:{line['tool']}")

    bundle = payload.get("fact_bundle") or {}

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
        "executed_actions": executed_actions,
        "tool_count": len(tool_lines),
        "has_failures": any(
            line.get("status") in ("error", "skipped") or line.get("error")
            for line in tool_lines
        ),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "engineering_trace": _engineering_trace_digest(state),
        "fact_bundle_id": bundle.get("fact_bundle_id") or payload.get("fact_bundle_id"),
        "rag_hit_count": int(bundle.get("rag_hit_count") or 0),
        "rag_domains": bundle.get("rag_domains") or [],
        "tools_used": bool(tool_lines),
    }


def _engineering_trace_digest(state: AgentState) -> Optional[dict[str, Any]]:
    if not state.get("trace_context") and not state.get("engineering_spans"):
        return None
    from app.services.engineering_trace import trace_summary

    return trace_summary(state)


def attach_turn_facts(state: AgentState) -> AgentState:
    """Rebuild turn_facts from state and store on state + payload."""
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
        if total_bytes < 500 and ("已写" in summary or "写入" in summary):
            warnings.append("summary claims substantial write but turn_facts bytes are small")

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
