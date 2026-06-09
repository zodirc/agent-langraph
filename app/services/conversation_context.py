"""多轮会话上下文：追加、压缩、finalize_turn_history、memory_writeback。

过滤不应进入下轮 LLM 的系统拒答等 surface 消息。

Conversation history and turn finalize for multi-turn sessions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.runtime.state import AgentState, TaskStatus, merge_state
from app.services.confirmation.stream_display import is_gate_reasoning_result
from app.services.context_compressor import (
    apply_semantic_context_compress,
    record_context_compress_metrics,
)

_SYSTEM_ASSISTANT_PREFIXES = (
    "task rejected",
    "task rejected by policy or human review",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_system_surface_message(content: str) -> bool:
    """Detect terminal boilerplate that should not train the next turn's LLM context."""
    text = str(content or "").strip().lower()
    if not text:
        return True
    return any(text.startswith(prefix) for prefix in _SYSTEM_ASSISTANT_PREFIXES)


def turn_outcome_from_status(status: str) -> str:
    if status == TaskStatus.COMPLETED.value:
        return "completed"
    if status == TaskStatus.WAITING_REVIEW.value:
        return "waiting_review"
    if status == TaskStatus.REJECTED.value:
        return "rejected"
    if status in (TaskStatus.FAILED.value, TaskStatus.DEAD_LETTER.value):
        return "failed"
    if status == TaskStatus.MISSION_PAUSED.value:
        return "paused"
    return "in_progress"


def append_message(
    history: list[dict[str, Any]],
    role: str,
    content: str,
    *,
    meta: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    text = str(content).strip()
    if not text:
        return history
    if history and history[-1].get("role") == role and history[-1].get("content") == text:
        return history
    msg: dict[str, Any] = {"role": role, "content": text, "at": _now_iso()}
    if meta:
        msg["meta"] = meta
    return list(history) + [msg]


def compress_conversation_history(
    history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not settings.SESSION_COMPRESS_ENABLED or not history:
        return history

    max_turns = settings.SESSION_MAX_HISTORY_TURNS
    max_chars = settings.SESSION_MAX_HISTORY_CHARS

    trimmed = history[-max_turns:] if len(history) > max_turns else list(history)
    total = sum(len(str(m.get("content", ""))) for m in trimmed)
    if total <= max_chars:
        return trimmed

    kept: list[dict[str, Any]] = []
    budget = max_chars
    for msg in reversed(trimmed):
        size = len(str(msg.get("content", "")))
        if size <= budget:
            kept.insert(0, msg)
            budget -= size
        else:
            break

    dropped = len(trimmed) - len(kept)
    if dropped > 0:
        kept.insert(
            0,
            {
                "role": "system",
                "content": f"[Earlier conversation: {dropped} message(s) omitted to fit context limit]",
                "at": _now_iso(),
                "meta": {"surface": "system", "outcome": "compressed"},
            },
        )
    if kept is not history:
        record_context_compress_metrics(history, kept, method="character")
    return kept


def compress_session_history(
    history: list[dict[str, Any]],
    *,
    state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Semantic compression when enabled; otherwise character-level trim."""
    if not history:
        return history
    if settings.CONTEXT_COMPRESS_SEMANTIC_ENABLED:
        try:
            compressed = apply_semantic_context_compress(history, state=state)
            if compressed is not history:
                return compressed
        except Exception:
            pass
    return compress_conversation_history(history)


def conversation_history_from_state(state: AgentState | dict[str, Any]) -> list[dict[str, Any]]:
    """
    Return the best available conversation history for this state.

    During an in-flight turn, user messages live in input_payload; after finalize,
    state.conversation_history is authoritative. Prefer the richer list when lengths differ.
    """
    payload = state.get("input_payload") or {}
    payload_hist = list(payload.get("conversation_history") or [])
    state_hist = list(state.get("conversation_history") or [])
    if not payload_hist:
        return state_hist
    if not state_hist:
        return payload_hist
    if len(payload_hist) >= len(state_hist):
        return payload_hist
    return state_hist


def conversation_history_for_llm(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """History entries exposed to planning/reasoning (drop system-surface boilerplate)."""
    out: list[dict[str, Any]] = []
    for msg in history:
        meta = msg.get("meta") if isinstance(msg.get("meta"), dict) else {}
        if meta.get("surface") == "system":
            continue
        role = str(msg.get("role") or "")
        content = str(msg.get("content") or "")
        if role == "assistant" and is_system_surface_message(content):
            continue
        out.append({"role": role, "content": content, "at": msg.get("at")})
    return out


def build_session_outcomes_digest(state: AgentState | dict[str, Any]) -> str:
    payload = state.get("input_payload") or {}
    outcomes = list(payload.get("session_outcomes") or [])
    if not outcomes:
        return ""
    lines = ["Recent session outcomes (not user-visible answers):"]
    for item in outcomes[-8:]:
        turn = item.get("turn", "?")
        outcome = item.get("outcome", "?")
        reason = str(item.get("reason") or "")[:120]
        line = f"- turn {turn}: {outcome}"
        if reason:
            line += f" ({reason})"
        lines.append(line)
    return "\n".join(lines)


def record_session_outcome(
    state: AgentState,
    *,
    outcome: str,
    reason: str = "",
) -> AgentState:
    payload = dict(state.get("input_payload") or {})
    outcomes = list(payload.get("session_outcomes") or [])
    outcomes.append(
        {
            "turn": int(state.get("session_turn") or 0),
            "outcome": outcome,
            "reason": reason[:240],
            "at": _now_iso(),
        }
    )
    payload["session_outcomes"] = outcomes[-24:]
    return merge_state(state, input_payload=payload)


def apply_conversation_history(
    state: AgentState,
    history: list[dict[str, Any]],
) -> AgentState:
    """Keep conversation_history in sync on state and input_payload."""
    payload = dict(state.get("input_payload") or {})
    payload["conversation_history"] = history
    return merge_state(
        state,
        conversation_history=history,
        input_payload=payload,
    )


def should_persist_assistant_turn(state: AgentState | dict[str, Any], answer: str) -> bool:
    if not str(answer or "").strip():
        return False
    status = str(state.get("status") or "")
    if status == TaskStatus.REJECTED.value:
        return False
    if is_system_surface_message(answer):
        return False
    return True


def resolve_turn_surface_answer(state: AgentState | dict[str, Any]) -> str:
    """
    Text to persist for this turn: finalized output, or reasoning summary when output
    did not run (e.g. human-review interrupt before output node).
    """
    status = str(state.get("status") or "")
    if status == TaskStatus.REJECTED.value:
        return ""

    answer = str(state.get("final_answer") or "").strip()
    if answer and not is_system_surface_message(answer):
        return answer

    if status in (
        TaskStatus.FAILED.value,
        TaskStatus.REASON_FAILED.value,
        TaskStatus.DEAD_LETTER.value,
    ):
        return ""

    reasoning = state.get("reasoning_result") or {}
    if is_gate_reasoning_result(reasoning):
        return ""

    summary = str(reasoning.get("summary") or "").strip()
    if summary and not is_system_surface_message(summary):
        return summary
    return ""


def persist_turn_draft_answer(state: AgentState) -> AgentState:
    """Ensure final_answer is set when reasoning produced a user-visible answer."""
    if str(state.get("final_answer") or "").strip():
        if is_system_surface_message(str(state.get("final_answer") or "")):
            return merge_state(state, final_answer=None)
        return state
    reasoning = state.get("reasoning_result") or {}
    structured = reasoning.get("structured") if isinstance(reasoning.get("structured"), dict) else {}
    from app.services.answer_compose import compose_user_answer

    composed = compose_user_answer(str(reasoning.get("summary") or ""), structured)
    answer = composed.strip() or resolve_turn_surface_answer(state)
    if not answer:
        return state
    return merge_state(state, final_answer=answer)


def finalize_turn_history(state: AgentState) -> AgentState:
    """Append assistant reply and sync history after any turn (including review interrupts)."""
    status = str(state.get("status") or "")
    answer = resolve_turn_surface_answer(state)
    history = conversation_history_from_state(state)

    if should_persist_assistant_turn(state, answer):
        history = append_message(
            history,
            "assistant",
            answer,
            meta={
                "turn": int(state.get("session_turn") or 0),
                "outcome": turn_outcome_from_status(status),
                "surface": "user",
            },
        )
        history = compress_session_history(history, state=state)
    elif status == TaskStatus.REJECTED.value:
        guard = state.get("output_guard_result") or {}
        issues = guard.get("issues") if isinstance(guard, dict) else []
        reason = ", ".join(str(i) for i in (issues or [])[:3]) or str(
            state.get("policy_result") or "rejected"
        )
        state = record_session_outcome(state, outcome="rejected", reason=reason)

    updated = apply_conversation_history(state, history)
    if answer and not str(updated.get("final_answer") or "").strip():
        updated = merge_state(updated, final_answer=answer)
    if should_persist_assistant_turn(state, answer) or str(updated.get("final_answer") or "").strip():
        from app.services.streaming_draft import clear_streaming_draft

        updated = clear_streaming_draft(updated)
    return updated


def write_turn_memories(state: AgentState) -> AgentState:
    """Best-effort long-term memory for completed or paused turns with surface content."""
    if not settings.SESSION_ENABLED:
        return state
    from app.services.memory_writeback_policy import should_index_episode

    if not should_index_episode(state):
        return state
    answer = resolve_turn_surface_answer(state)
    if not answer:
        return state
    status = str(state.get("status") or "")
    if status not in (
        TaskStatus.COMPLETED.value,
        TaskStatus.WAITING_REVIEW.value,
        TaskStatus.REVIEW_RESOLVED.value,
        TaskStatus.MISSION_PAUSED.value,
        TaskStatus.POLICY_CHECKED.value,
    ):
        return state
    try:
        from app.services.fact_layer import build_turn_facts
        from app.services.memory_store import get_memory_store

        memory_store = get_memory_store()
        turn_state = state
        if not turn_state.get("turn_facts"):
            turn_state = merge_state(turn_state, turn_facts=build_turn_facts(turn_state))
        memory_store.write_structured_episode(turn_state)

        history = conversation_history_from_state(turn_state)
        from app.services.prompt_context_gateway import (
            context_governance_enabled,
            governed_conversation_history,
        )

        if context_governance_enabled():
            llm_history = governed_conversation_history(
                turn_state, purpose="summarization"
            )
        else:
            llm_history = conversation_history_for_llm(history)
        if llm_history:
            snippets: list[str] = []
            for item in llm_history[-6:]:
                role = str(item.get("role") or "user")
                content = str(item.get("content") or "").strip()
                if content:
                    snippets.append(f"{role}: {content[:180]}")
            summary = " | ".join(snippets)[:1200]
            if summary:
                memory_store.create_session_summary(
                    task_id=turn_state["task_id"],
                    user_id=turn_state.get("user_id", "anonymous"),
                    task_type=turn_state.get("task_type", "qa"),
                    summary=summary,
                    conversation_history=llm_history,
                )
    except Exception:
        pass
    return state
