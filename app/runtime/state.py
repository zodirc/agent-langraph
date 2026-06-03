from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, TypedDict


class TaskStatus(str, Enum):
    NEW = "NEW"
    PLANNED = "PLANNED"
    RETRIEVED = "RETRIEVED"
    TOOL_EXECUTED = "TOOL_EXECUTED"
    WRITTEN = "WRITTEN"
    WRITING_FAILED = "WRITING_FAILED"
    REASONED = "REASONED"
    POLICY_CHECKED = "POLICY_CHECKED"
    WAITING_REVIEW = "WAITING_REVIEW"
    REVIEW_RESOLVED = "REVIEW_RESOLVED"
    COMPLETED = "COMPLETED"
    MISSION_RUNNING = "MISSION_RUNNING"
    MISSION_PAUSED = "MISSION_PAUSED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    TOOL_FAILED = "TOOL_FAILED"
    REASON_FAILED = "REASON_FAILED"
    DEAD_LETTER = "DEAD_LETTER"
    ABANDONED = "ABANDONED"


class AgentState(TypedDict):
    task_id: str
    session_id: str
    user_id: str
    task_type: str
    input_payload: dict[str, Any]
    current_node: str
    status: str

    plan: Optional[list[str]]
    selected_tools: Optional[list[str]]
    skip_retrieval: Optional[bool]
    conversation_history: Optional[list[dict[str, Any]]]
    session_turn: Optional[int]
    tool_results: Optional[list[dict[str, Any]]]
    retrieved_knowledge: Optional[list[dict[str, Any]]]
    reasoning_result: Optional[dict[str, Any]]
    reflection_result: Optional[dict[str, Any]]
    reflection_count: Optional[int]
    reasoning_mode: Optional[str]
    token_budget: Optional[dict[str, Any]]
    cost_budget: Optional[dict[str, Any]]
    output_guard_result: Optional[dict[str, Any]]
    policy_result: Optional[str]
    review_required: bool
    review_feedback: Optional[dict[str, Any]]

    final_answer: Optional[str]
    artifacts: Optional[list[dict[str, Any]]]
    structured_output: Optional[dict[str, Any]]

    memory_hits: Optional[list[dict[str, Any]]]
    turn_facts: Optional[dict[str, Any]]
    turn_event_log: Optional[dict[str, Any]]
    trace_context: Optional[dict[str, Any]]
    engineering_spans: Optional[list[dict[str, Any]]]
    trace_active_span: Optional[dict[str, Any]]
    manuscript: Optional[dict[str, Any]]
    audit_log: list[dict[str, Any]]
    errors: list[str]
    retry_count: int
    node_history: list[dict[str, Any]]
    review_requested_at: Optional[str]

    # Mission runtime (long-horizon ReAct control loop)
    mission: Optional[dict[str, Any]]
    progress: Optional[dict[str, Any]]
    observation: Optional[dict[str, Any]]
    observations: Optional[list[dict[str, Any]]]
    step_decision: Optional[dict[str, Any]]
    mission_step: Optional[int]
    mission_control: Optional[dict[str, Any]]

    # Exploration runtime (Ch21 pilot)
    exploration: Optional[dict[str, Any]]

    # Phase 5 — Supervisor-Worker (architecture §20)
    execution_mode: Optional[str]
    subtasks: Optional[list[dict[str, Any]]]
    worker_results: Optional[dict[str, Any]]

    # Self-Routed Deliberation Loop (SRDL) — bounded ReAct in single runtime
    react_loop: Optional[dict[str, Any]]

    # Skill platform (policy layer)
    skill_id: Optional[str]
    skill_version: Optional[str]
    skill_snapshot: Optional[dict[str, Any]]
    skill_source_type: Optional[str]
    skill_runtime_policy: Optional[dict[str, Any]]


def create_initial_state(
    *,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    user_id: str = "anonymous",
    task_type: str = "qa",
    input_payload: Optional[dict[str, Any]] = None,
) -> AgentState:
    tid = task_id or str(uuid.uuid4())
    return ensure_agent_state(
        {
            "task_id": tid,
            "session_id": session_id or tid,
            "user_id": user_id,
            "task_type": task_type,
            "input_payload": input_payload or {},
            "current_node": "api",
            "status": TaskStatus.NEW.value,
            "plan": None,
            "selected_tools": None,
            "skip_retrieval": None,
            "conversation_history": None,
            "session_turn": None,
            "tool_results": None,
            "retrieved_knowledge": None,
            "reasoning_result": None,
            "reflection_result": None,
            "reflection_count": 0,
            "reasoning_mode": None,
            "token_budget": None,
            "cost_budget": None,
            "output_guard_result": None,
            "policy_result": None,
            "review_required": False,
            "review_feedback": None,
            "final_answer": None,
            "artifacts": None,
            "structured_output": None,
            "memory_hits": None,
            "turn_facts": None,
            "turn_event_log": None,
            "trace_context": None,
            "engineering_spans": None,
            "trace_active_span": None,
            "manuscript": None,
            "audit_log": [],
            "errors": [],
            "retry_count": 0,
            "node_history": [],
            "review_requested_at": None,
            "mission": None,
            "progress": None,
            "observation": None,
            "observations": None,
            "step_decision": None,
            "mission_step": None,
            "mission_control": None,
            "exploration": None,
            "execution_mode": "single",
            "subtasks": None,
            "worker_results": None,
            "react_loop": None,
        }
    )


def ensure_agent_state(state: AgentState | Mapping[str, Any]) -> AgentState:
    """Validate and normalize state through AgentStateModel (graph boundaries)."""
    from app.runtime.agent_state_model import model_to_state, state_to_model

    return model_to_state(state_to_model(dict(state)))


def merge_state(state: AgentState, **updates: Any) -> AgentState:
    """
    Merge state updates while preserving nested dict fields.

    LangGraph update chunks often contain partial `input_payload` / `progress` objects.
    A shallow overwrite can drop keys like `progress.work_plan`, causing the UI/control
    loop to disagree on step status (e.g., "pending" resurrecting after "done").
    """
    merged = dict(state)
    for key, value in updates.items():
        if (
            key == "node_history"
            and isinstance(value, list)
            and isinstance(merged.get(key), list)
        ):
            # Stream snapshots may carry truncated node_history (or only current node).
            # Keep the richer in-memory history instead of regressing to a shorter list.
            current = list(merged.get(key) or [])
            incoming = list(value or [])
            merged[key] = incoming if len(incoming) >= len(current) else current
            continue
        if (
            key in (
                "input_payload",
                "progress",
                "mission",
                "manuscript",
                "observation",
                "mission_control",
                "react_loop",
            )
            and isinstance(value, dict)
            and isinstance(merged.get(key), dict)
        ):
            merged[key] = {**dict(merged.get(key) or {}), **dict(value)}
        else:
            merged[key] = value
    return ensure_agent_state(merged)


def record_node_transition(
    state: AgentState,
    node: str,
    *,
    status: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Append node execution to history timeline (architecture §15.2)."""
    entry: dict[str, Any] = {
        "node": node,
        "status": status or state.get("status"),
        "at": datetime.now(timezone.utc).isoformat(),
    }
    if detail:
        entry["detail"] = detail
    return list(state.get("node_history", [])) + [entry]


def append_node_history(
    state: AgentState,
    node: str,
    *,
    status: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
) -> AgentState:
    history = record_node_transition(state, node, status=status, detail=detail)
    updates: dict[str, Any] = {"node_history": history, "current_node": node}
    if status:
        updates["status"] = status
    return merge_state(state, **updates)


def append_audit(
    state: AgentState,
    node: str,
    action: str,
    detail: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    entry: dict[str, Any] = {"node": node, "action": action}
    if detail:
        entry["detail"] = detail
    return list(state.get("audit_log", [])) + [entry]
