from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal
from app.domain.agent_message import AgentCard, AgentMessage
from app.services.agent_registry import get_agent_registry
from app.services.a2a_dispatch import dispatch_message, runtime_agent_card
from app.services.auth_service import AuthPrincipal
from app.services.graph_runner import get_graph_runner

router = APIRouter(prefix="/a2a", tags=["a2a"])


class A2AMessageRequest(BaseModel):
    message: dict[str, Any] = Field(description="AgentMessage JSON envelope")
    parent_task_id: Optional[str] = None


class A2AMessageResponse(BaseModel):
    result: dict[str, Any]
    parent_task_id: str


class AgentRegisterRequest(BaseModel):
    url: str
    card: dict[str, Any] = Field(default_factory=dict)
    ttl_sec: Optional[int] = None


@router.get("/agents")
def list_registered_agents(
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    return {"agents": get_agent_registry().list_agents()}


@router.post("/agents/register")
def register_agent(
    request: AgentRegisterRequest,
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, str]:
    card_data = request.card or runtime_agent_card().to_dict()
    card = AgentCard.from_dict(card_data)
    get_agent_registry().register(card, request.url, ttl_sec=request.ttl_sec)
    return {"agent_id": card.agent_id, "status": "registered"}


@router.post("/agents/{agent_id}/heartbeat")
def agent_heartbeat(
    agent_id: str,
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, str]:
    if not get_agent_registry().heartbeat(agent_id):
        raise HTTPException(status_code=404, detail="Agent not registered")
    return {"agent_id": agent_id, "status": "ok"}


@router.get("/agent-card")
def get_agent_card(
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    """Runtime Agent Card for cross-service discovery (A2A skeleton)."""
    return runtime_agent_card().to_dict()


@router.post("/messages", response_model=A2AMessageResponse)
def post_a2a_message(
    request: A2AMessageRequest,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> A2AMessageResponse:
    """
    Dispatch a capability-based AgentMessage to a domain worker (HTTP A2A endpoint).
    """
    try:
        message = AgentMessage.from_dict(request.message)
    except (KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid message: {exc}") from exc

    parent_id = request.parent_task_id
    if not parent_id:
        state = get_graph_runner().start_task(
            user_id=principal.user_id,
            task_type="a2a",
            input_payload={"goal": "a2a dispatch", "execution_mode": "single"},
        )
        parent_id = state["task_id"]

    result_msg = dispatch_message(
        message,
        parent_task_id=parent_id,
        user_id=principal.user_id,
    )
    return A2AMessageResponse(result=result_msg.to_dict(), parent_task_id=parent_id)
