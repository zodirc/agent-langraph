from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal
from app.services.auth_service import AuthPrincipal
from app.services.batch_runner import get_batch_runner
from app.services.batch_store import get_batch_store
from app.services.input_guard import sanitize_input_payload

router = APIRouter(prefix="/batches", tags=["batches"])


class BatchTaskItem(BaseModel):
    task_type: str = "qa"
    input_payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=0, ge=0, le=100)


class CreateBatchRequest(BaseModel):
    tasks: list[BatchTaskItem] = Field(min_length=1)


class CreateBatchResponse(BaseModel):
    batch_id: str
    status: str
    total: int


@router.post("", response_model=CreateBatchResponse, status_code=202)
def create_batch(
    request: CreateBatchRequest,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> CreateBatchResponse:
    if len(request.tasks) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 tasks per batch")
    items: list[dict[str, Any]] = []
    for task in request.tasks:
        try:
            payload = sanitize_input_payload(task.input_payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        items.append(
            {
                "task_type": task.task_type,
                "input_payload": payload,
                "priority": task.priority,
            }
        )

    store = get_batch_store()
    batch_id, _ = store.create_batch(user_id=principal.user_id, items=items)
    get_batch_runner().submit_batch(batch_id, principal.user_id)
    batch = store.get_batch(batch_id)
    return CreateBatchResponse(
        batch_id=batch_id,
        status=batch["status"] if batch else "PENDING",
        total=len(items),
    )


@router.get("")
def list_batches(
    limit: int = 20,
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    batches = get_batch_store().list_batches(limit=min(limit, 100))
    return {"batches": batches, "total": len(batches)}


@router.get("/{batch_id}")
def get_batch(
    batch_id: str,
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    batch = get_batch_store().get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail=f"Batch not found: {batch_id}")
    return batch
