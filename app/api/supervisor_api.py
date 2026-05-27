from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal
from app.api.task_api import CreateTaskResponse, TaskStatusResponse, _prepare_task_request
from app.api.task_api import CreateTaskRequest
from app.services.auth_service import AuthPrincipal
from app.services.graph_runner import get_graph_runner
from app.services.state_store import get_state_store

router = APIRouter(prefix="/supervisor", tags=["supervisor"])


class SupervisorTaskRequest(BaseModel):
    goal: str
    domains: Optional[list[str]] = None
    task_type: str = "supervisor"
    user_id: Optional[str] = None
    extra_payload: dict[str, Any] = Field(default_factory=dict)


@router.post("/tasks", response_model=CreateTaskResponse, status_code=201)
def create_supervisor_task(
    request: SupervisorTaskRequest,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> CreateTaskResponse:
    payload = {"goal": request.goal, "execution_mode": "supervisor", **request.extra_payload}
    if request.domains:
        payload["domains"] = request.domains
    task_request = CreateTaskRequest(
        task_type=request.task_type,
        user_id=request.user_id,
        input_payload=payload,
    )
    user_id, task_type, prepared = _prepare_task_request(task_request, principal)
    try:
        state = get_graph_runner().start_task(
            user_id=user_id,
            task_type=task_type,
            input_payload=prepared,
            execution_mode="supervisor",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return CreateTaskResponse(
        task_id=state["task_id"],
        status=str(state["status"]),
        current_node=state["current_node"],
    )


@router.post("/tasks/stream")
def stream_supervisor_task(
    request: SupervisorTaskRequest,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> StreamingResponse:
    payload = {"goal": request.goal, "execution_mode": "supervisor", **request.extra_payload}
    if request.domains:
        payload["domains"] = request.domains
    task_request = CreateTaskRequest(
        task_type=request.task_type,
        user_id=request.user_id,
        input_payload=payload,
    )
    user_id, task_type, prepared = _prepare_task_request(task_request, principal)
    try:
        generator = get_graph_runner().stream_task(
            user_id=user_id,
            task_type=task_type,
            input_payload=prepared,
            execution_mode="supervisor",
        )
        return StreamingResponse(generator, media_type="text/event-stream")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/tasks/{task_id}/status", response_model=TaskStatusResponse)
def supervisor_task_status(
    task_id: str,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> TaskStatusResponse:
    state = get_state_store().load(task_id)
    if not state or state.get("execution_mode") != "supervisor":
        raise HTTPException(status_code=404, detail=f"Supervisor task not found: {task_id}")
    return TaskStatusResponse(
        task_id=task_id,
        status=str(state["status"]),
        current_node=state["current_node"],
        review_required=bool(state.get("review_required")),
        errors=list(state.get("errors", [])),
    )


@router.get("/tasks/{task_id}/result")
def supervisor_task_result(
    task_id: str,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    state = get_state_store().load(task_id)
    if not state or state.get("execution_mode") != "supervisor":
        raise HTTPException(status_code=404, detail=f"Supervisor task not found: {task_id}")
    return {
        "task_id": task_id,
        "status": state.get("status"),
        "final_answer": state.get("final_answer"),
        "subtasks": state.get("subtasks"),
        "worker_results": state.get("worker_results"),
        "structured_output": state.get("structured_output"),
    }
