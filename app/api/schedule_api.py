from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal, require_role
from app.config.settings import settings
from app.services.auth_service import AuthPrincipal
from app.services.input_guard import sanitize_input_payload
from app.services.schedule_store import get_schedule_store
from app.services.scheduler_service import get_scheduler_service

router = APIRouter(prefix="/schedules", tags=["schedules"])


class CreateScheduleRequest(BaseModel):
    name: str
    cron_expression: str = Field(description="Standard 5-field cron, e.g. '0 */6 * * *'")
    task_type: str = "qa"
    input_payload: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    priority: int = Field(default=0, ge=0, le=100)


@router.post("", status_code=201)
def create_schedule(
    request: CreateScheduleRequest,
    principal: AuthPrincipal = Depends(require_role("user", "admin")),
) -> dict[str, Any]:
    if not settings.SCHEDULER_ENABLED:
        raise HTTPException(status_code=400, detail="Scheduler is disabled")
    try:
        payload = sanitize_input_payload(request.input_payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    schedule = get_schedule_store().create(
        name=request.name,
        cron_expression=request.cron_expression,
        user_id=principal.user_id,
        task_type=request.task_type,
        input_payload=payload,
        enabled=request.enabled,
        priority=request.priority,
    )
    if request.enabled:
        get_scheduler_service().add_schedule(schedule)
    return schedule


@router.get("")
def list_schedules(
    limit: int = 50,
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    schedules = get_schedule_store().list_all(limit=min(limit, 100))
    return {"schedules": schedules, "total": len(schedules)}


@router.get("/{schedule_id}")
def get_schedule(
    schedule_id: str,
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    schedule = get_schedule_store().get(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}")
    return schedule


@router.delete("/{schedule_id}")
def delete_schedule(
    schedule_id: str,
    _principal: AuthPrincipal = Depends(require_role("admin")),
) -> dict[str, str]:
    store = get_schedule_store()
    if not store.get(schedule_id):
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}")
    store.delete(schedule_id)
    get_scheduler_service().remove_schedule(schedule_id)
    return {"schedule_id": schedule_id, "status": "deleted"}
