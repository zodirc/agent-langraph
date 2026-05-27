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
    "compress_conversation_history",
    "compress_session_history",
    "finalize_turn_history",
    "graph_thread_id",
    "prepare_session_turn",
]


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
    return merge_state(
        state,
        input_payload=payload,
        conversation_history=history,
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
        mission_step=0,
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
    """
    Resolve state for this user message.

    When session mode is on and session_id is set, one session maps to one task_id
    (task_id == session_id). Returns (state, created_new_task).
    """
    goal = str(
        payload.get("goal") or payload.get("query") or payload.get("question") or ""
    ).strip()

    if not settings.SESSION_ENABLED or not session_id:
        history = append_message([], "user", goal) if goal else []
        if history:
            payload = {**payload, "conversation_history": history}
        state = create_initial_state(
            user_id=user_id,
            task_type=task_type,
            input_payload=payload,
        )
        state = apply_conversation_history(merge_state(state, session_turn=1), history)
        return state, True

    store = get_state_store()
    task_id = session_id
    existing = store.load(task_id) if not new_session else None

    if existing and existing.get("session_id") == session_id:
        history = conversation_history_from_state(existing)
        if goal:
            history = append_message(history, "user", goal)
        history = compress_session_history(history, state=existing)
        merged = {
            **existing.get("input_payload", {}),
            **payload,
            "conversation_history": history,
        }
        from app.services.session.config import load_session_turn_policy_config
        from app.services.session.turn_policy import (
            apply_qa_turn_isolation,
            resolve_session_turn,
            restore_archived_mission,
        )

        if goal:
            decision = resolve_session_turn(existing, merged, goal, incoming=payload)
            if load_session_turn_policy_config().audit_decisions:
                merged["turn_policy_decision"] = decision.to_dict()

            if decision.intent == "resume_mission":
                merged = restore_archived_mission(existing, merged)
            elif existing.get("mission") and decision.intent == "isolate_qa":
                merged = apply_qa_turn_isolation(merged, existing)
        if goal:
            from app.services.mission_steer import (
                _append_steer_goal,
                apply_steer_planning_gate,
                steer_needs_planning_llm,
            )

            merged = _append_steer_goal(merged, goal)
            from app.services.mission_steer_outcome_confirm import clear_steer_outcome_flags

            merged = clear_steer_outcome_flags(merged)
            merged.pop("steer_outcome_confirmed", None)
            merged.pop("steer_outcome_confirmed_for", None)
            mission_active = bool(existing.get("mission")) and not merged.get(
                "mission_suspended"
            )
            if mission_active and steer_needs_planning_llm(message=goal):
                merged = apply_steer_planning_gate(merged)
                merged["writing_intent"] = {
                    "enabled": False,
                    "source": "await_steer_planning",
                }
                merged.pop("current_work_item", None)
                merged["skip_planning_llm"] = False
            from app.services.mission_steer import (
                apply_review_outline_mode,
                goal_requests_outline_read,
            )

            if mission_active and goal_requests_outline_read(goal):
                merged = apply_review_outline_mode(
                    merged, existing.get("mission") or {}
                )
        elif not merged.get("goal"):
            merged["goal"] = str(existing.get("input_payload", {}).get("goal") or "")
        payload = merged
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
                "session_artifacts": ms.to_dict(),
            }
        else:
            payload = enrich_payload(payload, task_id, session_turn=turn, manuscript=ms)
        state = _reset_execution_fields(existing, payload)
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
    state = create_initial_state(
        task_id=task_id,
        session_id=session_id,
        user_id=user_id,
        task_type=task_type,
        input_payload=payload,
    )
    state = apply_conversation_history(merge_state(state, session_turn=1), history)
    return state, True
