"""Top-level AgentState Pydantic model (Batch 4 phase 1 — coexists with TypedDict)."""

from __future__ import annotations

from typing import Any, Optional, cast

from pydantic import BaseModel, ConfigDict, Field

from app.runtime.state import AgentState, TaskStatus
from app.runtime.state_models import coerce_agent_state


class AgentStateModel(BaseModel):
    """Validated view of AgentState for boundaries and tooling."""

    model_config = ConfigDict(extra="allow")

    task_id: str
    session_id: str
    user_id: str = "anonymous"
    task_type: str = "qa"
    input_payload: dict[str, Any] = Field(default_factory=dict)
    current_node: str = "api"
    status: str = TaskStatus.NEW.value
    plan: Optional[list[str]] = None
    selected_tools: Optional[list[str]] = None
    skip_retrieval: Optional[bool] = None
    retrieval_decision: Optional[dict[str, Any]] = None
    query_object: Optional[dict[str, Any]] = None
    evidence_packets: Optional[list[dict[str, Any]]] = None
    retrieval_trace: Optional[dict[str, Any]] = None
    evidence_conflicts: Optional[list[dict[str, Any]]] = None
    failure_attribution: Optional[dict[str, Any]] = None
    task_drift: Optional[dict[str, Any]] = None
    conversation_history: Optional[list[dict[str, Any]]] = None
    session_turn: Optional[int] = None
    tool_results: Optional[list[dict[str, Any]]] = None
    retrieved_knowledge: Optional[list[dict[str, Any]]] = None
    reasoning_result: Optional[dict[str, Any]] = None
    reflection_result: Optional[dict[str, Any]] = None
    reflection_count: Optional[int] = 0
    reasoning_mode: Optional[str] = None
    token_budget: Optional[dict[str, Any]] = None
    cost_budget: Optional[dict[str, Any]] = None
    output_guard_result: Optional[dict[str, Any]] = None
    policy_result: Optional[str] = None
    review_required: bool = False
    review_feedback: Optional[dict[str, Any]] = None
    final_answer: Optional[str] = None
    artifacts: Optional[list[dict[str, Any]]] = None
    structured_output: Optional[dict[str, Any]] = None
    memory_hits: Optional[list[dict[str, Any]]] = None
    turn_facts: Optional[dict[str, Any]] = None
    turn_event_log: Optional[dict[str, Any]] = None
    trace_context: Optional[dict[str, Any]] = None
    engineering_spans: Optional[list[dict[str, Any]]] = None
    trace_active_span: Optional[dict[str, Any]] = None
    manuscript: Optional[dict[str, Any]] = None
    audit_log: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    retry_count: int = 0
    node_history: list[dict[str, Any]] = Field(default_factory=list)
    review_requested_at: Optional[str] = None
    observation: Optional[dict[str, Any]] = None
    observations: Optional[list[dict[str, Any]]] = None
    execution_mode: Optional[str] = "single"
    skill_id: Optional[str] = None
    skill_version: Optional[str] = None
    skill_snapshot: Optional[dict[str, Any]] = None
    skill_source_type: Optional[str] = None
    skill_runtime_policy: Optional[dict[str, Any]] = None
    interrupt_context: Optional[dict[str, Any]] = None
    execution_run: Optional[dict[str, Any]] = None
    pending_user_message: Optional[dict[str, Any]] = None
    event_type: Optional[str] = None
    event_id: Optional[str] = None
    foreground_status: Optional[dict[str, Any]] = None
    background_status: Optional[dict[str, Any]] = None
    plan_graph: Optional[dict[str, Any]] = None
    plan_invalidations: Optional[dict[str, Any]] = None
    execution_version: Optional[int] = 1
    context_budget_buckets: Optional[dict[str, Any]] = None
    verification_result: Optional[dict[str, Any]] = None
    submission_decision: Optional[dict[str, Any]] = None
    eval_capture: Optional[dict[str, Any]] = None


def state_to_model(state: AgentState | dict[str, Any]) -> AgentStateModel:
    coerced = coerce_agent_state(dict(state))
    return AgentStateModel.model_validate(coerced)


def model_to_state(model: AgentStateModel) -> AgentState:
    return cast(AgentState, coerce_agent_state(model.model_dump()))
