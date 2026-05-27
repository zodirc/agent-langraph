from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal
from app.runtime.state import TaskStatus
from app.services.audit_store import get_audit_store
from app.services.auth_service import AuthPrincipal
from app.services.graph_runner import get_graph_runner
from app.services.graph_execution_pool import GraphExecutionRejected
from app.services.tenant_quota import TenantQuotaExceeded
from app.services.input_guard import sanitize_input_payload
from app.services.state_store import get_state_store

router = APIRouter(prefix="/tasks", tags=["tasks"])


class CreateTaskRequest(BaseModel):
    task_type: str = "qa"
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    new_session: bool = False
    input_payload: dict[str, Any] = Field(default_factory=dict)


class CreateTaskResponse(BaseModel):
    task_id: str
    status: str
    current_node: str


class NodeHistoryEntry(BaseModel):
    node: str
    status: str
    at: str
    detail: Optional[dict[str, Any]] = None


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    current_node: str
    review_required: bool
    errors: list[str]
    node_history: list[NodeHistoryEntry] = Field(default_factory=list)
    review_requested_at: Optional[str] = None


class TaskResultResponse(BaseModel):
    task_id: str
    status: str
    final_answer: Optional[str] = None
    structured_output: Optional[dict[str, Any]] = None
    artifacts: Optional[list[dict[str, Any]]] = None


class TaskSummary(BaseModel):
    task_id: str
    task_type: str
    status: str
    current_node: str
    goal: Optional[str] = None
    updated_at: str


class TaskListResponse(BaseModel):
    tasks: list[TaskSummary]
    total: int


def _prepare_task_request(
    request: CreateTaskRequest,
    principal: AuthPrincipal,
) -> tuple[str, str, dict[str, Any]]:
    try:
        payload = sanitize_input_payload(request.input_payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    user_id = request.user_id or principal.user_id
    payload.setdefault("user_role", principal.role)
    return user_id, request.task_type, payload


@router.get("", response_model=TaskListResponse)
def list_tasks(
    limit: int = 20,
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> TaskListResponse:
    records = get_state_store().list_recent_tasks(limit=min(limit, 100))
    tasks = [
        TaskSummary(
            task_id=r.task_id,
            task_type=r.task_type,
            status=r.status,
            current_node=r.current_node,
            goal=str(
                r.input_payload.get("goal")
                or r.input_payload.get("query")
                or r.input_payload.get("question")
                or ""
            )[:120]
            or None,
            updated_at=r.updated_at,
        )
        for r in records
    ]
    return TaskListResponse(tasks=tasks, total=len(tasks))


@router.post("/stream")
def stream_task(
    request: CreateTaskRequest,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> StreamingResponse:
    """SSE stream of node-by-node execution progress."""
    user_id, task_type, payload = _prepare_task_request(request, principal)
    try:
        mode = str(payload.get("execution_mode", "single"))
        generator = get_graph_runner().stream_task(
            user_id=user_id,
            task_type=task_type,
            input_payload=payload,
            execution_mode=mode,
            session_id=request.session_id,
            new_session=request.new_session,
        )
        return StreamingResponse(generator, media_type="text/event-stream")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("", response_model=CreateTaskResponse, status_code=201)
def create_task(
    request: CreateTaskRequest,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> CreateTaskResponse:
    user_id, task_type, payload = _prepare_task_request(request, principal)
    try:
        mode = str(payload.get("execution_mode", "single"))
        state = get_graph_runner().start_task(
            user_id=user_id,
            task_type=task_type,
            input_payload=payload,
            execution_mode=mode,
            session_id=request.session_id,
            new_session=request.new_session,
        )
        return CreateTaskResponse(
            task_id=state["task_id"],
            status=str(state["status"]),
            current_node=state["current_node"],
        )
    except GraphExecutionRejected as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "execution_pool_saturated",
                "active": exc.active,
                "max_concurrent": exc.max_concurrent,
            },
        ) from exc
    except TenantQuotaExceeded as exc:
        raise HTTPException(status_code=429, detail=exc.to_detail()) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class MissionInterventionModel(BaseModel):
    action: str = Field(
        description=(
            "rewrite_outline | review_outline | reset_body | edit_plot | run_tools | "
            "pause | continue | enqueue_work"
        )
    )
    force: bool = Field(
        default=False,
        description="When true, bypass normal step_policy and honor this action",
    )
    edit_spec: dict[str, Any] = Field(default_factory=dict)
    tools: list[str] = Field(default_factory=list)
    tool_params: dict[str, Any] = Field(default_factory=dict)
    work_item: Optional[dict[str, Any]] = None
    use_planning: bool = False
    reason: str = ""


class SteerTaskRequest(BaseModel):
    message: str = Field(default="", description="Natural language steer (optional)")
    intervention: Optional[MissionInterventionModel] = None
    priority: int = Field(
        default=0,
        description=(
            "Steer priority (0=normal). Higher values request faster preemption inside long writing steps."
        ),
    )
    preempt: bool = Field(
        default=False,
        description=(
            "Best-effort preemption hint. When true, long writing steps will stop earlier when safe."
        ),
    )
    confirm: bool = Field(
        default=False,
        description="Structured approval for pending steer intent/outcome gate",
    )


class SteerTaskResponse(BaseModel):
    task_id: str
    status: str
    revision_intent: Optional[str] = None
    message: str = ""
    client_display: Optional[dict[str, Any]] = None


class ResumeTaskResponse(BaseModel):
    task_id: str
    status: str
    current_node: str
    final_answer: Optional[str] = None
    steer_intent_pending_confirm: bool = False
    steer_intent_confirmation: Optional[dict[str, Any]] = None
    steer_outcome_pending_confirm: bool = False
    steer_outcome_confirmation: Optional[dict[str, Any]] = None
    confirmation_actions: Optional[dict[str, Any]] = None


@router.post("/{task_id}/steer", response_model=SteerTaskResponse)
def steer_task(
    task_id: str,
    request: SteerTaskRequest,
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> SteerTaskResponse:
    """
    Inject user guidance during or between orchestrated mission steps.

    While MISSION_RUNNING, the message is queued for the next step boundary.
    While MISSION_PAUSED, it is applied immediately.
    """
    if not request.message.strip() and not request.intervention and not request.confirm:
        raise HTTPException(
            status_code=400,
            detail="Provide message, intervention, and/or confirm=true",
        )
    try:
        intervention = (
            request.intervention.model_dump(exclude_none=True)
            if request.intervention
            else None
        )
        state = get_graph_runner().steer_mission(
            task_id,
            request.message,
            intervention=intervention,
            confirm=request.confirm,
            priority=int(request.priority or 0),
            preempt=bool(request.preempt),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    payload = state.get("input_payload") or {}
    from app.services.mission_steer import pending_steer_is_set

    queued = pending_steer_is_set(state.get("pending_user_message"))
    from app.services.client_display import build_steer_task_client_display

    client_display = build_steer_task_client_display(state, queued=queued)
    return SteerTaskResponse(
        task_id=task_id,
        status=str(state["status"]),
        revision_intent=payload.get("revision_intent"),
        message=str(client_display.get("kind") or ("queued" if queued else "applied")),
        client_display=client_display,
    )


class ResumeTaskRequest(BaseModel):
    confirm: bool = Field(
        default=False,
        description=(
            "Approve pending steer confirmation: intent (before execute) "
            "or outcome (after work item)"
        ),
    )


@router.post("/{task_id}/resume", response_model=ResumeTaskResponse)
def resume_task(
    task_id: str,
    request: ResumeTaskRequest = ResumeTaskRequest(),
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> ResumeTaskResponse:
    """Run the next orchestrated work item after MISSION_PAUSED."""
    try:
        state = get_graph_runner().resume_mission(task_id, confirm=request.confirm)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    from app.services.confirmation.stream_display import (
        build_gate_sse_fields,
        client_final_answer,
    )

    gate_fields = build_gate_sse_fields(task_id, state)
    return ResumeTaskResponse(
        task_id=task_id,
        status=str(state["status"]),
        current_node=str(state["current_node"]),
        final_answer=client_final_answer(state),
        **gate_fields,
    )


@router.post("/{task_id}/resume/stream")
def stream_resume_task(
    task_id: str,
    request: ResumeTaskRequest = ResumeTaskRequest(),
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> StreamingResponse:
    """SSE stream for mission resume (progress, writing_delta, confirmation gates)."""
    try:
        generator = get_graph_runner().stream_resume_mission(
            task_id, confirm=request.confirm
        )
        return StreamingResponse(generator, media_type="text/event-stream")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{task_id}/status", response_model=TaskStatusResponse)
def get_task_status(
    task_id: str,
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> TaskStatusResponse:
    state = get_state_store().load(task_id, read_only=True)
    if not state:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    history = [
        NodeHistoryEntry(
            node=str(item.get("node", "")),
            status=str(item.get("status", "")),
            at=str(item.get("at", "")),
            detail=item.get("detail"),
        )
        for item in (state.get("node_history") or [])
    ]
    return TaskStatusResponse(
        task_id=task_id,
        status=str(state["status"]),
        current_node=state["current_node"],
        review_required=bool(state.get("review_required")),
        errors=list(state.get("errors", [])),
        node_history=history,
        review_requested_at=state.get("review_requested_at"),
    )


@router.get("/{task_id}/result", response_model=TaskResultResponse)
def get_task_result(
    task_id: str,
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> TaskResultResponse:
    state = get_state_store().load(task_id, read_only=True)
    if not state:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    status = str(state["status"])
    if status not in (
        TaskStatus.COMPLETED.value,
        TaskStatus.REJECTED.value,
        TaskStatus.WAITING_REVIEW.value,
        TaskStatus.REVIEW_RESOLVED.value,
    ):
        raise HTTPException(
            status_code=409,
            detail=f"Task not ready for result. Current status: {status}",
        )
    return TaskResultResponse(
        task_id=task_id,
        status=status,
        final_answer=state.get("final_answer"),
        structured_output=state.get("structured_output"),
        artifacts=state.get("artifacts"),
    )


@router.get("/{task_id}/conversation")
def get_task_conversation(
    task_id: str,
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    """Return multi-turn conversation stored on the task (session window)."""
    state = get_state_store().load(task_id, read_only=True)
    if not state:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return {
        "task_id": task_id,
        "session_id": state.get("session_id"),
        "session_turn": state.get("session_turn"),
        "conversation_history": state.get("conversation_history") or [],
    }


@router.get("/{task_id}/audit")
def get_task_audit(
    task_id: str,
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    chain = get_audit_store().get_chain(task_id)
    if not chain:
        state = get_state_store().load(task_id, read_only=True)
        if not state:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
        chain = state.get("audit_log", [])
    return {"task_id": task_id, "audit_chain": chain}
