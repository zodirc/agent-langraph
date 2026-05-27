from __future__ import annotations

from typing import Any, Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal
from app.services.auth_service import AuthPrincipal
from app.services.feedback_service import record_feedback

router = APIRouter(prefix="/feedback", tags=["feedback"])


class FeedbackRequest(BaseModel):
    task_id: str
    rating: Union[int, str] = Field(description="1-5 or positive/negative/neutral")
    comment: str = ""
    outcome: str = Field(default="unknown", description="success | failure | partial | unknown")
    tags: list[str] = Field(default_factory=list)
    experiment_tag: str = Field(default="", description="A/B experiment label")


@router.post("")
def submit_feedback(
    request: FeedbackRequest,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    """Record task outcome feedback for learning and retrieval (Ch9)."""
    try:
        return record_feedback(
            task_id=request.task_id,
            user_id=principal.user_id,
            rating=request.rating,
            comment=request.comment,
            outcome=request.outcome,
            tags=request.tags,
            experiment_tag=request.experiment_tag,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
