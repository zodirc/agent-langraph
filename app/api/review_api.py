from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal, require_role
from app.services.auth_service import AuthPrincipal
from app.services.graph_runner import get_graph_runner
from app.services.state_store import get_state_store

router = APIRouter(prefix="/reviews", tags=["reviews"])


class SubmitReviewRequest(BaseModel):
    task_id: str
    action: str = Field(description="APPROVE or REJECT")
    comment: str = ""


class SubmitReviewResponse(BaseModel):
    task_id: str
    status: str
    current_node: str


@router.post("", response_model=SubmitReviewResponse)
def submit_review(
    request: SubmitReviewRequest,
    _principal: AuthPrincipal = Depends(require_role("user", "admin")),
) -> SubmitReviewResponse:
    action = request.action.upper()
    if action not in ("APPROVE", "REJECT"):
        raise HTTPException(status_code=400, detail="action must be APPROVE or REJECT")

    stored = get_state_store().load(request.task_id)
    if not stored:
        raise HTTPException(status_code=404, detail=f"Task not found: {request.task_id}")

    try:
        state = get_graph_runner().submit_review(
            request.task_id,
            action=action,
            comment=request.comment,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return SubmitReviewResponse(
        task_id=state["task_id"],
        status=str(state["status"]),
        current_node=state["current_node"],
    )
