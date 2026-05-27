from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_current_principal
from app.services.auth_service import AuthPrincipal
from app.services.dead_letter_store import get_dead_letter_store
from app.services.graph_runner import get_graph_runner

router = APIRouter(prefix="/dead-letter", tags=["dead-letter"])


class RequeueResponse(BaseModel):
    task_id: str
    status: str
    message: str


@router.get("")
def list_dead_letters(
    limit: int = 50,
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    entries = get_dead_letter_store().list_entries(limit=min(limit, 200))
    return {"entries": entries, "total": len(entries)}


@router.get("/{task_id}")
def get_dead_letter(
    task_id: str,
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    entry = get_dead_letter_store().get(task_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Dead letter not found: {task_id}")
    return entry


@router.post("/{task_id}/requeue", response_model=RequeueResponse)
def requeue_dead_letter(
    task_id: str,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> RequeueResponse:
    """Re-enqueue DLQ task for execution (§22.4)."""
    state = get_dead_letter_store().requeue(task_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Dead letter not found or already resolved: {task_id}")
    final = get_graph_runner().start_task(
        user_id=state["user_id"],
        task_type=state["task_type"],
        input_payload=state["input_payload"],
        task_id=state["task_id"],
        execution_mode=str(state.get("execution_mode", "single")),
    )
    return RequeueResponse(
        task_id=task_id,
        status=str(final["status"]),
        message="Task requeued and executed",
    )
