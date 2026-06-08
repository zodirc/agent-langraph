"""多轮会话：session_id 映射 task_id，每轮独立 checkpoint 线程。

prepare_session_turn 解析续聊、steer、mission 恢复；graph_thread_id 隔离 LangGraph 状态。

Multi-turn session turn resolution and per-turn checkpoint thread ids.
"""

from __future__ import annotations

from typing import Any, Optional

from app.config.settings import settings
from app.runtime.state import AgentState, TaskStatus, create_initial_state, merge_state
from app.services.conversation_context import (
    append_message,
    apply_conversation_history,
    compress_conversation_history,
    compress_session_history,
    conversation_history_from_state,
    finalize_turn_history,
)
from app.services.manuscript_service import enrich_payload, resolve_manuscript
from app.services.state_store import get_state_store

__all__ = [
    "append_message",
    "build_inbound_merged_payload",
    "compress_conversation_history",
    "compress_session_history",
    "finalize_turn_history",
    "graph_thread_id",
    "prepare_session_turn",
]


def _stamp_turn_policy_and_classification(
    existing: AgentState,
    merged: dict[str, Any],
    *,
    goal: str,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Resolve turn_policy + authoritative event classification before turn increment."""
    from app.services.event_classification import (
        classify_user_event,
        stamp_inbound_classification,
    )
    from app.services.session.config import load_session_turn_policy_config
    from app.services.session.turn_policy import (
        apply_qa_turn_isolation,
        resolve_session_turn,
        restore_archived_mission,
    )

    if not goal:
        return merged

    decision = resolve_session_turn(existing, merged, goal, incoming=incoming)
    if load_session_turn_policy_config().audit_decisions:
        merged["turn_policy_decision"] = decision.to_dict()

    active_mission = existing.get("mission") or (existing.get("input_payload") or {}).get(
        "mission"
    )
    if decision.intent in ("resume_mission", "supersede_active_mission"):
        merged = restore_archived_mission(existing, merged)
    if decision.intent == "resume_mission":
        from app.services.mission_execution import (
            is_mechanical_resume_decision,
            issue_execution_grant_to_payload,
            mechanical_resume_allowed,
        )
        from app.services.mission_steer import steer_needs_planning_llm

        if is_mechanical_resume_decision(decision) and not steer_needs_planning_llm(message=goal):
            if mechanical_resume_allowed(existing, decision):
                merged = issue_execution_grant_to_payload(merged, source=str(decision.source))
    elif active_mission and decision.intent == "isolate_qa":
        merged = apply_qa_turn_isolation(merged, existing)

    history = merged.get("conversation_history") or []
    prospective_turn = int(existing.get("session_turn") or 0) + 1
    classify_state = merge_state(
        existing,
        conversation_history=history,
        session_turn=prospective_turn,
    )
    classification = classify_user_event(classify_state, payload=merged)
    return stamp_inbound_classification(merged, classification)


def build_inbound_merged_payload(
    existing: AgentState,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Merge history, turn_policy, and L1 classification for an existing session turn.

    Used by L0 ingress and ``prepare_session_turn`` before ``session_turn`` increments.
    """
    goal = str(
        payload.get("goal") or payload.get("query") or payload.get("question") or ""
    ).strip()
    history = conversation_history_from_state(existing)
    if goal:
        history = append_message(history, "user", goal)
    history = compress_session_history(history, state=existing)
    merged = {
        **existing.get("input_payload", {}),
        **payload,
        "conversation_history": history,
    }
    if goal:
        merged = _stamp_turn_policy_and_classification(
            existing,
            merged,
            goal=goal,
            incoming=payload,
        )
    elif not merged.get("goal"):
        merged["goal"] = str(existing.get("input_payload", {}).get("goal") or "")
    return merged


def graph_thread_id(state: AgentState) -> str:
    """Isolate LangGraph checkpoints per session turn."""
    turn = int(state.get("session_turn") or 1)
    return f"{state['task_id']}:t{turn}"


def _reset_execution_fields(state: AgentState, payload: dict[str, Any]) -> AgentState:
    turn = int(state.get("session_turn") or 0) + 1
    progress = dict(state.get("progress") or {})
    if progress:
        progress["phase"] = "executing"
        progress["consecutive_failures"] = 0
    history = conversation_history_from_state(
        {"input_payload": payload, "conversation_history": state.get("conversation_history")}
    )
    from app.services.reasoning_shortcut import clear_turn_carryover

    payload = clear_turn_carryover({**payload, "conversation_history": history})
    preserve_mission = bool(state.get("mission")) and not payload.get("mission_suspended")
    mission_step = int(state.get("mission_step") or 0) if preserve_mission else 0
    return merge_state(
        state,
        input_payload=payload,
        conversation_history=history,
        node_history=[],
        status=TaskStatus.NEW.value,
        current_node="api",
        session_turn=turn,
        plan=None,
        selected_tools=None,
        skip_retrieval=None,
        tool_results=None,
        retrieved_knowledge=None,
        memory_hits=None,
        manuscript=state.get("manuscript"),
        reasoning_result=None,
        policy_result=None,
        review_required=False,
        review_feedback=None,
        final_answer=None,
        structured_output=None,
        errors=[],
        retry_count=0,
        mission_step=mission_step,
        observation=None,
        step_decision=None,
        mission_control=None,
        token_budget=None,
        cost_budget=None,
        progress=progress or state.get("progress"),
        execution_mode=payload.get("execution_mode") or state.get("execution_mode"),
    )


def prepare_session_turn(
    *,
    session_id: Optional[str],
    user_id: str,
    task_type: str,
    payload: dict[str, Any],
    new_session: bool = False,
) -> tuple[AgentState, bool]:
    """为本轮用户消息解析 AgentState；返回 (state, created)。

    One session maps to one task_id when SESSION_ENABLED; created=True for new task.
    """
    goal = str(
        payload.get("goal") or payload.get("query") or payload.get("question") or ""
    ).strip()

    if not settings.SESSION_ENABLED or not session_id:
        history = append_message([], "user", goal) if goal else []
        if history:
            payload = {**payload, "conversation_history": history}
        from app.services.event_classification import (
            classify_user_event,
            stamp_inbound_classification,
        )

        state = create_initial_state(
            user_id=user_id,
            task_type=task_type,
            input_payload=payload,
        )
        state = apply_conversation_history(merge_state(state, session_turn=1), history)
        if goal:
            classification = classify_user_event(state, payload=payload)
            payload = stamp_inbound_classification(payload, classification)
            state = merge_state(state, input_payload=payload)
        return state, True

    store = get_state_store()
    task_id = session_id
    existing = store.load(task_id) if not new_session else None
    if existing:
        from app.services.mission_worker_lost import reconcile_worker_lost

        existing = reconcile_worker_lost(existing)

    if existing and existing.get("session_id") == session_id:
        payload = build_inbound_merged_payload(existing, payload)
        from app.services.mission_intervention import (
            apply_intervention_to_payload,
            intervention_from_payload,
        )

        intervention = intervention_from_payload(payload)
        if intervention:
            payload = apply_intervention_to_payload(payload, intervention)

        turn = int(existing.get("session_turn") or 0) + 1
        ms = resolve_manuscript(task_id, existing.get("manuscript"))
        if payload.get("mission_suspended"):
            payload = {
                **payload,
                "manuscript": ms.to_dict(),
            }
        else:
            payload = enrich_payload(payload, task_id, session_turn=turn, manuscript=ms)
        from app.services.session_fsm import stamp_session_mode, sync_fsm_state

        payload = stamp_session_mode(payload)
        state = _reset_execution_fields(existing, payload)
        state = sync_fsm_state(state)
        if payload.get("mission_suspended"):
            state = merge_state(
                state,
                mission=None,
                execution_mode="single",
                manuscript=ms.to_dict(),
            )
        else:
            state = merge_state(state, manuscript=ms.to_dict())
        return state, False

    history = append_message([], "user", goal) if goal else []
    payload = {**payload, "conversation_history": history}
    from app.services.event_classification import (
        classify_user_event,
        stamp_inbound_classification,
    )
    from app.services.session_fsm import stamp_session_mode

    payload = stamp_session_mode(payload)
    state = create_initial_state(
        task_id=task_id,
        session_id=session_id,
        user_id=user_id,
        task_type=task_type,
        input_payload=payload,
    )
    state = apply_conversation_history(merge_state(state, session_turn=1), history)
    if goal:
        classification = classify_user_event(state, payload=payload)
        payload = stamp_inbound_classification(payload, classification)
        state = merge_state(state, input_payload=payload)
    return state, True
