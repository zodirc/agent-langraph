"""Session alias routes — session_id equals task_id in copilot mode."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.api.deps import require_task_access_dep
from app.api.task_api import get_task_conversation, get_task_message_events, get_task_messages
from app.services.auth_service import AuthPrincipal

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("/{session_id}/messages")
def get_session_messages(
    session_id: str,
    _principal: AuthPrincipal = Depends(require_task_access_dep),
):
    return get_task_messages(session_id, _principal=_principal)


@router.get("/{session_id}/messages/events")
def get_session_message_events(
    session_id: str,
    after_seq: int = Query(0, ge=0),
    limit: int = Query(2000, ge=1, le=5000),
    _principal: AuthPrincipal = Depends(require_task_access_dep),
):
    return get_task_message_events(
        session_id,
        after_seq=after_seq,
        limit=limit,
        _principal=_principal,
    )


@router.get("/{session_id}/messages/stream")
def stream_session_message_events(
    session_id: str,
    after_seq: int = Query(0, ge=0),
    tail: bool = Query(False),
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> StreamingResponse:
    from app.api.task_api import stream_task_message_events

    return stream_task_message_events(
        session_id,
        after_seq=after_seq,
        tail=tail,
        _principal=_principal,
    )


@router.get("/{session_id}/conversation")
def get_session_conversation(
    session_id: str,
    _principal: AuthPrincipal = Depends(require_task_access_dep),
):
    return get_task_conversation(session_id, _principal=_principal)
