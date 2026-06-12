"""任务 HTTP API。

创建与执行：POST /tasks（同步创建）；新会话首条消息走 POST /tasks/{id}/message/stream。
_prepare_task_request：sanitize_input_payload，可选 attach_skill_to_payload（仅 payload）。
Mission 控制：POST /tasks/{id}/message/stream（唯一发消息入口）；stop/pause/cancel/interrupt-stream。
查询：GET /tasks/{id}/status、result、audit、llm-interactions 与任务列表。

Task HTTP API for create, stream, mission steer/resume/stop, and status queries.
_prepare_task_request sanitizes input and attaches skill policy to payload only.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal, require_task_access_dep
from app.api.tenant_access import assert_task_access
from app.runtime.state import TaskStatus
from app.services.audit_store import get_audit_store
from app.services.auth_service import AuthPrincipal
from app.services.graph_runner import get_graph_runner
from app.services.graph_execution_pool import GraphExecutionRejected
from app.services.tenant_quota import TenantQuotaExceeded
from app.services.input_guard import sanitize_input_payload
from app.services.live_task_state import get_live
from app.services.state_debug_view import build_task_state_debug_response
from app.services.state_store import get_state_store

router = APIRouter(prefix="/tasks", tags=["tasks"])


class CreateTaskRequest(BaseModel):
    task_type: str = "qa"
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    new_session: bool = False
    skill_id: Optional[str] = None
    skill_params: dict[str, Any] = Field(default_factory=dict)
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
    executor_active: bool = False
    pause_reason: Optional[str] = None
    pending_steer_queued: bool = False
    foreground_operation: Optional[dict[str, Any]] = None
    latest_steer_message: Optional[str] = None
    intent_revision: Optional[int] = None
    turn_contract_primary_op: Optional[str] = None
    fsm_state: Optional[str] = None
    session_mode: Optional[str] = None
    streaming_answer_text: Optional[str] = None
    streaming_answer_active: bool = False
    streaming_thinking_text: Optional[str] = None
    live_running: bool = False
    final_answer: Optional[str] = None


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


class TaskDeleteResponse(BaseModel):
    task_id: str
    deleted: bool


def wrap_task_sse_stream(generator: Iterator[str]) -> Iterator[str]:
    """Yield SSE from graph runner; convert setup failures to error events."""
    try:
        yield from generator
    except Exception as exc:
        payload = {"detail": str(exc), "status": "STREAM_ERROR"}
        yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _prepare_task_request(
    request: CreateTaskRequest,
    principal: AuthPrincipal,
) -> tuple[str, str, dict[str, Any]]:
    """
    归一化 payload；有 skill_id 时在图执行前解析策略。

    Normalize payload and attach skill policy before graph runs.
    """
    try:
        payload = sanitize_input_payload(request.input_payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    user_id = request.user_id or principal.user_id
    payload.setdefault("user_role", principal.role)
    if request.skill_id:
        from app.config.settings import settings
        from app.services.skill_resolver import (
            SkillNotAvailableError,
            SkillNotFoundError,
            SkillPermissionError,
            SkillResolveError,
            attach_skill_to_payload,
        )
        from app.services.tenant_context import get_tenant_id

        if not settings.SKILL_RUNTIME_POLICY_ENABLED:
            raise HTTPException(status_code=400, detail="Skill runtime policy is disabled")
        try:
            params = dict(request.skill_params or {})
            if params.get("goal") and "goal" not in payload:
                payload["goal"] = params["goal"]
            if params.get("query") and "query" not in payload:
                payload["query"] = params["query"]
            payload = attach_skill_to_payload(
                payload,
                skill_id=request.skill_id.strip(),
                skill_params=params,
                user_role=principal.role,
                tenant_id=get_tenant_id(),
            )
        except SkillNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except SkillPermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except SkillNotAvailableError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SkillResolveError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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


@router.delete("/{task_id}", response_model=TaskDeleteResponse)
def delete_task(
    task_id: str,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> TaskDeleteResponse:
    import time

    from app.api.tenant_access import assert_task_access
    from app.services.task_control import request_cancel
    from app.services.task_tombstone import mark_task_tombstone

    stored = get_state_store().load(task_id, read_only=True)
    if stored:
        assert_task_access(principal, task_id)

    try:
        request_cancel(task_id, requested_by="delete_api", reason="task_deleted")
    except Exception:
        pass
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        from app.services.graph_run_registry import is_graph_run_active

        if not is_graph_run_active(task_id):
            break
        time.sleep(0.1)

    session_turn_hint = int(stored.get("session_turn") or 1) if stored else 1

    if stored:
        get_state_store().delete_task(task_id)

    from app.services.task_cleanup import purge_task_remains

    mark_task_tombstone(task_id)
    purge_task_remains(task_id, session_turn_hint=session_turn_hint)
    return TaskDeleteResponse(task_id=task_id, deleted=True)


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
    intent_anchor: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional edit anchors: old_text, new_text, steer_correction, target_hint (outline|body)",
    )
    work_item: Optional[dict[str, Any]] = None
    use_planning: bool = False
    reason: str = ""


class StopTaskResponse(BaseModel):
    task_id: str
    status: str
    message: str
    client_display: Optional[dict[str, Any]] = None


class TaskControlRequest(BaseModel):
    reason: str = "user_requested"
    requested_by: str = "web"
    worker_id: Optional[str] = Field(
        default=None,
        description="Optional OMAW worker scope id (current_work_item.id or oma_capability)",
    )


class TaskControlResponse(BaseModel):
    task_id: str
    accepted: bool
    control_action: str
    effective_state: str
    active_step_id: Optional[str] = None
    run_id: Optional[str] = None
    worker_id: Optional[str] = None


class TaskControlSnapshotResponse(BaseModel):
    task_id: str
    running: bool
    run_id: Optional[str] = None
    effective_state: str
    stream_interrupted: bool = False
    pause_requested: bool = False
    cancel_requested: bool = False
    active_step_id: Optional[str] = None
    last_committed_step: Optional[dict[str, Any]] = None
    interrupt_context: Optional[dict[str, Any]] = None
    worker_controls: Optional[dict[str, Any]] = None


class MessageTaskRequest(BaseModel):
    message: str = Field(default="", description="User message text")
    client_message_id: Optional[str] = Field(
        default=None,
        description="Idempotency key — duplicate submits are ignored",
    )
    confirm: bool = Field(default=False, description="Approve pending steer confirmation gate")
    intervention: Optional[MissionInterventionModel] = None
    priority: int = Field(default=0)
    meta: dict[str, Any] = Field(default_factory=dict)


@router.post("/{task_id}/message/stream")
def stream_message_task(
    task_id: str,
    request: MessageTaskRequest,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> StreamingResponse:
    """Unified message ingress — single stream for steer/resume/supersede/new turn."""
    if not request.message.strip() and not request.intervention and not request.confirm:
        raise HTTPException(
            status_code=400,
            detail="Provide message, intervention, and/or confirm=true",
        )
    stored = get_state_store().load(task_id, read_only=True)
    if stored:
        assert_task_access(principal, task_id)
    intervention = (
        request.intervention.model_dump(exclude_none=True)
        if request.intervention
        else None
    )
    from app.services.session_controller import get_session_controller

    def safe_generator():
        stream_done = False
        try:
            for chunk in get_session_controller().handle_message(
                task_id,
                request.message,
                client_message_id=request.client_message_id,
                meta=request.meta,
                confirm=request.confirm,
                intervention=intervention,
                priority=int(request.priority or 0),
                user_id=principal.user_id,
                new_session=bool((request.meta or {}).get("new_session")),
            ):
                if chunk.startswith("event: done"):
                    stream_done = True
                yield chunk
        except KeyError as exc:
            payload = {"task_id": task_id, "detail": str(exc), "status": "NOT_FOUND"}
            yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except ValueError as exc:
            payload = {"task_id": task_id, "detail": str(exc), "status": "INVALID_MESSAGE"}
            yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception as exc:
            payload = {"task_id": task_id, "detail": str(exc), "status": "STREAM_ERROR"}
            yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        if not stream_done:
            done = {"task_id": task_id, "status": "FAILED"}
            yield f"event: done\ndata: {json.dumps(done, ensure_ascii=False)}\n\n"

    return StreamingResponse(safe_generator(), media_type="text/event-stream")


@router.post("/{task_id}/stop", response_model=StopTaskResponse)
def stop_task(
    task_id: str,
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> StopTaskResponse:
    """
    Pause a running mission at the next safe checkpoint (alias for POST /pause).
    """
    try:
        control = get_graph_runner().pause_task(task_id, reason="user requested stop")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    stored = get_state_store().load(task_id) or {}
    from app.services.client_display import build_stop_task_client_display

    display = build_stop_task_client_display(
        stored,
        effective_state=str(control.get("effective_state") or "pause_requested"),
    )
    return StopTaskResponse(
        task_id=task_id,
        status=str(stored.get("status") or ""),
        message=str(control.get("effective_state") or "pause_requested"),
        client_display=display,
    )


@router.post("/{task_id}/interrupt-stream", response_model=TaskControlResponse)
def interrupt_task_stream(
    task_id: str,
    request: TaskControlRequest = TaskControlRequest(),
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> TaskControlResponse:
    """Stop SSE output only; task continues in background."""
    result = get_graph_runner().interrupt_task_stream(
        task_id,
        requested_by=request.requested_by,
        reason=request.reason,
    )
    return TaskControlResponse(**result)


@router.post("/{task_id}/pause", response_model=TaskControlResponse)
def pause_task(
    task_id: str,
    request: TaskControlRequest = TaskControlRequest(),
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> TaskControlResponse:
    """Request task pause at next safe checkpoint."""
    result = get_graph_runner().pause_task(
        task_id,
        requested_by=request.requested_by,
        reason=request.reason,
        worker_id=request.worker_id,
    )
    return TaskControlResponse(**result)


@router.post("/{task_id}/cancel", response_model=TaskControlResponse)
def cancel_task(
    task_id: str,
    request: TaskControlRequest = TaskControlRequest(),
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> TaskControlResponse:
    """Cancel task; discard uncommitted step buffer, keep committed artifacts."""
    result = get_graph_runner().cancel_task(
        task_id,
        requested_by=request.requested_by,
        reason=request.reason,
        worker_id=request.worker_id,
    )
    return TaskControlResponse(**result)


@router.post("/{task_id}/workers/{worker_id}/pause", response_model=TaskControlResponse)
def pause_worker(
    task_id: str,
    worker_id: str,
    request: TaskControlRequest = TaskControlRequest(),
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> TaskControlResponse:
    """Request pause for a specific OMAW worker scope."""
    result = get_graph_runner().pause_task(
        task_id,
        requested_by=request.requested_by,
        reason=request.reason,
        worker_id=worker_id,
    )
    return TaskControlResponse(**result)


@router.post("/{task_id}/workers/{worker_id}/cancel", response_model=TaskControlResponse)
def cancel_worker(
    task_id: str,
    worker_id: str,
    request: TaskControlRequest = TaskControlRequest(),
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> TaskControlResponse:
    """Cancel a specific OMAW worker scope."""
    result = get_graph_runner().cancel_task(
        task_id,
        requested_by=request.requested_by,
        reason=request.reason,
        worker_id=worker_id,
    )
    return TaskControlResponse(**result)


@router.get("/{task_id}/control", response_model=TaskControlSnapshotResponse)
def get_task_control(
    task_id: str,
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> TaskControlSnapshotResponse:
    """Observability for in-flight control state and interrupt_context."""
    try:
        snapshot = get_graph_runner().get_task_control_snapshot(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return TaskControlSnapshotResponse(**snapshot)


@router.get("/{task_id}/state")
def get_task_state_debug(
    task_id: str,
    truncate: bool = True,
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> dict[str, Any]:
    """
    Full AgentState snapshot for the current task (session id == task id in copilot mode).

    Used by Web CLI state inspector.

    Returns store (DB), live (in-process stream), and merged views when available.
    """
    store_state = get_state_store().load(task_id, read_only=True)
    live_entry = get_live(task_id)
    if not store_state and not live_entry:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return build_task_state_debug_response(
        task_id=task_id,
        store_state=store_state,
        live_entry=live_entry,
        truncate=truncate,
    )


@router.get("/{task_id}/session-usage")
def get_task_session_usage(
    task_id: str,
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> dict[str, Any]:
    """Provider-reported token usage for chat panel (no heuristic estimates)."""
    from app.services.prompt_context_gateway import resolve_context_panel_meta

    state = get_state_store().load(task_id, read_only=True)
    if not state:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return {
        "task_id": task_id,
        "session": resolve_context_panel_meta(state),
    }


@router.get("/models/catalog")
def get_models_catalog(
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    """Available models and context window sizes for chat metering UI."""
    from app.services.model_catalog import catalog_for_api

    return {"models": catalog_for_api()}


@router.get("/{task_id}/context-composition")
def get_task_context_composition(
    task_id: str,
    purpose: str = Query("reasoning", description="Context policy purpose"),
    model: Optional[str] = Query(
        None,
        description="Catalog model id for context-window preview (same session context)",
    ),
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> dict[str, Any]:
    """
    Prompt composition debug view (ADR Context Governance §11.3).

    Builds a fresh envelope from current store+live state without invoking the LLM.
    """
    from app.services.prompt_context_gateway import build_prompt_composition_for_state
    from app.services.state_debug_view import build_merged_debug_state

    store_state = get_state_store().load(task_id, read_only=True)
    live_entry = get_live(task_id)
    if not store_state and not live_entry:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    if live_entry is not None:
        state = (
            build_merged_debug_state(store_state, live_entry.state)
            if store_state
            else live_entry.state
        )
    else:
        state = store_state
    allowed = {
        "planning",
        "reasoning",
        "writing",
        "reviewing",
        "reflection",
        "routing",
        "summarization",
        "code_agent",
    }
    p = purpose if purpose in allowed else "reasoning"
    from app.services.prompt_context_gateway import resolve_context_panel_meta

    model_id = (model or "").strip() or None
    composition = build_prompt_composition_for_state(
        state, purpose=p, model_id=model_id
    )  # type: ignore[arg-type]
    session_meta = resolve_context_panel_meta(state, purpose=p, model_id=model_id)
    return {
        "task_id": task_id,
        "purpose": p,
        "composition": composition,
        "session": session_meta,
    }


class ChatModelRequest(BaseModel):
    model_id: str = Field(..., min_length=1, max_length=256)


@router.put("/{task_id}/chat-model")
def put_task_chat_model(
    task_id: str,
    body: ChatModelRequest,
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> dict[str, Any]:
    """
    Persist per-session model choice (same conversation context, different context window).
    """
    from app.runtime.state import merge_state
    from app.services.prompt_context_gateway import resolve_context_panel_meta

    store = get_state_store()
    state = store.load(task_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    payload = dict(state.get("input_payload") or {})
    payload["chat_model_id"] = body.model_id.strip()
    updated = merge_state(state, input_payload=payload)  # type: ignore[arg-type]
    store.save(updated)
    return {
        "task_id": task_id,
        "chat_model_id": body.model_id,
        "session": resolve_context_panel_meta(updated),
    }


class ContextCompressRequest(BaseModel):
    scope: str = Field(
        default="transcript",
        description="transcript | all_compressible | aggressive",
    )
    token_budget: Optional[int] = Field(default=None, ge=1024, le=64800)


@router.post("/{task_id}/context/compress")
def post_task_context_compress(
    task_id: str,
    body: ContextCompressRequest,
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> dict[str, Any]:
    """
    Policy-constrained manual context compress (ADR §1.1 #6).
    """
    from app.services.prompt_context_gateway import manual_compress_context

    state = get_state_store().load(task_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    updated, envelope = manual_compress_context(
        state,
        scope=body.scope,
        token_budget=body.token_budget,
    )
    get_state_store().save(updated)
    from app.services.prompt_context_gateway import (
        build_prompt_composition_for_state,
        resolve_context_panel_meta,
    )

    composition = build_prompt_composition_for_state(updated, purpose="reasoning")  # type: ignore[arg-type]
    return {
        "task_id": task_id,
        "scope": body.scope,
        "token_budget": envelope.token_budget_total,
        "kept": len(envelope.items_kept),
        "dropped": len(envelope.items_dropped),
        "compressed": len(envelope.items_compressed),
        "composition": composition,
        "session": resolve_context_panel_meta(updated),
    }


@router.get("/{task_id}/status", response_model=TaskStatusResponse)
def get_task_status(
    task_id: str,
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> TaskStatusResponse:
    from app.services.graph_run_registry import executor_active_for_state
    from app.services.live_task_state import get_live
    from app.services.session_fsm import get_fsm_state, session_mode, sync_fsm_state
    from app.services.state_debug_view import build_merged_debug_state
    from app.services.streaming_draft import resolve_streaming_draft, resolve_thinking_text_for_restore

    state = get_state_store().load(task_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    state = sync_fsm_state(state)
    live_entry = get_live(task_id)
    if live_entry is not None:
        state = build_merged_debug_state(state, live_entry.state)
    fsm = get_fsm_state(state)
    mode = session_mode(state)
    history = [
        NodeHistoryEntry(
            node=str(item.get("node", "")),
            status=str(item.get("status", "")),
            at=str(item.get("at", "")),
            detail=item.get("detail"),
        )
        for item in (state.get("node_history") or [])
    ]
    display_node = str(state.get("current_node") or "")
    mission_control = state.get("mission_control") if isinstance(state.get("mission_control"), dict) else {}
    payload = state.get("input_payload") or {}
    ctx = state.get("interrupt_context") or {}
    fg_op = ctx.get("foreground_operation") if isinstance(ctx, dict) else None
    contract = payload.get("turn_contract") if isinstance(payload.get("turn_contract"), dict) else {}
    latest_steer = str(payload.get("latest_steer_message") or "").strip() or None
    streaming = resolve_streaming_draft(state)
    from app.services.confirmation.stream_display import client_final_answer

    return TaskStatusResponse(
        task_id=task_id,
        status=str(state["status"]),
        current_node=display_node,
        review_required=bool(state.get("review_required")),
        errors=list(state.get("errors", [])),
        node_history=history,
        review_requested_at=state.get("review_requested_at"),
        executor_active=executor_active_for_state(state),
        pause_reason=str(mission_control.get("pause_reason") or "") or None,
        pending_steer_queued=bool(
            isinstance(state.get("pending_user_message"), dict)
            and (state.get("pending_user_message") or {}).get("messages")
        ),
        foreground_operation=dict(fg_op) if isinstance(fg_op, dict) else None,
        latest_steer_message=latest_steer,
        intent_revision=int(payload.get("intent_revision") or 0) or None,
        turn_contract_primary_op=str(contract.get("primary_op") or "") or None,
        fsm_state=fsm,
        session_mode=mode,
        streaming_answer_text=streaming.get("text") if streaming else None,
        streaming_answer_active=bool(streaming and streaming.get("active")),
        streaming_thinking_text=resolve_thinking_text_for_restore(state),
        live_running=bool(live_entry and live_entry.running),
        final_answer=client_final_answer(state),
    )


@router.get("/{task_id}/result", response_model=TaskResultResponse)
def get_task_result(
    task_id: str,
    _principal: AuthPrincipal = Depends(require_task_access_dep),
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
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> dict[str, Any]:
    """Return multi-turn conversation stored on the task (session window)."""
    from app.services.live_task_state import get_live
    from app.services.state_debug_view import build_merged_debug_state
    from app.services.streaming_draft import resolve_streaming_draft

    state = get_state_store().load(task_id, read_only=True)
    if not state:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    live_entry = get_live(task_id)
    if live_entry is not None:
        state = build_merged_debug_state(state, live_entry.state)
    streaming = resolve_streaming_draft(state)
    return {
        "task_id": task_id,
        "session_id": state.get("session_id"),
        "session_turn": state.get("session_turn"),
        "conversation_history": state.get("conversation_history") or [],
        "streaming_answer_text": streaming.get("text") if streaming else None,
        "streaming_answer_active": bool(streaming and streaming.get("active")),
    }


@router.get("/{task_id}/messages")
def get_task_messages(
    task_id: str,
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> dict[str, Any]:
    """Message-centric session view (Phase 2 read model)."""
    from app.services.chat_message_service import get_chat_message_service
    from app.services.live_task_state import get_live
    from app.services.state_debug_view import build_merged_debug_state

    state = get_state_store().load(task_id, read_only=True)
    if not state:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    live_entry = get_live(task_id)
    if live_entry is not None:
        state = build_merged_debug_state(state, live_entry.state)
    view = get_chat_message_service().build_session_view(task_id, task_state=state)
    view["live_running"] = bool(get_live(task_id) and get_live(task_id).running)
    return view


@router.get("/{task_id}/messages/events")
def get_task_message_events(
    task_id: str,
    after_seq: int = Query(0, ge=0),
    limit: int = Query(2000, ge=1, le=5000),
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> dict[str, Any]:
    """Event log for stream resume (Phase 3)."""
    from app.services.stream_event_resume import list_events_json

    state = get_state_store().load(task_id, read_only=True)
    if not state:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return list_events_json(task_id, after_seq=after_seq, limit=limit)


@router.get("/{task_id}/messages/stream")
def stream_task_message_events(
    task_id: str,
    after_seq: int = Query(0, ge=0),
    tail: bool = Query(False, description="Poll for new events while task is live-running"),
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> StreamingResponse:
    """Replay persisted stream events as SSE; optional live tail for断线续播."""
    from app.services.stream_event_resume import iter_resumed_sse

    state = get_state_store().load(task_id, read_only=True)
    live = get_live(task_id)
    if not state and not live:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    def generator():
        yield from iter_resumed_sse(task_id, after_seq=after_seq, tail_live=tail)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{task_id}/audit")
def get_task_audit(
    task_id: str,
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> dict[str, Any]:
    chain = get_audit_store().get_chain(task_id)
    if not chain:
        state = get_state_store().load(task_id, read_only=True)
        if not state:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
        chain = state.get("audit_log", [])
    return {"task_id": task_id, "audit_chain": chain}


@router.get("/{task_id}/llm-interactions")
def get_task_llm_interactions(
    task_id: str,
    index: Optional[int] = Query(
        None,
        ge=1,
        description="1-based 序号，仅返回第 N 次交互（监控页筛选）",
    ),
    summary: bool = Query(
        False,
        description="仅返回摘要列表（序号、purpose、字数），不含正文",
    ),
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> dict[str, Any]:
    """LLM request/response log for a task (session_id equals task_id in session mode)."""
    from app.services.llm_interaction_store import get_llm_interaction_store

    store = get_llm_interaction_store()
    state = get_state_store().load(task_id, read_only=True)
    count = store.count_for_task(task_id)
    if not state and count == 0:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    session_id = (state or {}).get("session_id") or task_id
    if summary:
        return {
            "task_id": task_id,
            "session_id": session_id,
            "count": count,
            "total_bytes": store.bytes_for_task(task_id),
            "retention": store.retention_limits(),
            "summaries": store.list_summaries_for_task(task_id),
        }

    if index is not None:
        one = store.get_by_index(task_id, index)
        if not one:
            raise HTTPException(
                status_code=404,
                detail=f"LLM interaction #{index} not found for task {task_id}",
            )
        return {
            "task_id": task_id,
            "session_id": session_id,
            "count": count,
            "index": index,
            "interaction": one,
        }

    interactions = store.list_for_task(task_id)
    return {
        "task_id": task_id,
        "session_id": session_id,
        "count": len(interactions),
        "interactions": interactions,
    }
