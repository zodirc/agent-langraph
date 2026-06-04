"""任务 HTTP API。

创建与执行：POST /tasks、POST /tasks/stream → _prepare_task_request → GraphRunner。
_prepare_task_request：sanitize_input_payload，可选 attach_skill_to_payload（仅 payload）。
Mission 控制：POST /tasks/{id}/steer、resume、stop。
查询：GET /tasks/{id}/status、result、audit、llm-interactions 与任务列表。

Task HTTP API for create, stream, mission steer/resume/stop, and status queries.
_prepare_task_request sanitizes input and attaches skill policy to payload only.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal, require_task_access_dep
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
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> TaskDeleteResponse:
    stored = get_state_store().load(task_id, read_only=True)
    if not stored:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    session_turn_hint = int(stored.get("session_turn") or 1)

    deleted = get_state_store().delete_task(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    from app.services.task_cleanup import purge_task_remains

    purge_task_remains(task_id, session_turn_hint=session_turn_hint)
    return TaskDeleteResponse(task_id=task_id, deleted=True)


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
    replace_goal: bool = Field(
        default=False,
        description=(
            "When true, replace current mission goal with new message "
            "(takeover mode), instead of appending steer text."
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


class StopTaskResponse(BaseModel):
    task_id: str
    status: str
    message: str
    client_display: Optional[dict[str, Any]] = None


@router.post("/{task_id}/steer", response_model=SteerTaskResponse)
def steer_task(
    task_id: str,
    request: SteerTaskRequest,
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> SteerTaskResponse:
    """
    Mission 纠偏 API；RUNNING 排队，PAUSED 立即生效；confirm 走确认门。

    Inject steer during or between mission steps; see mission_steer module doc.
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
            replace_goal=bool(request.replace_goal),
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


@router.post("/{task_id}/stop", response_model=StopTaskResponse)
def stop_task(
    task_id: str,
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> StopTaskResponse:
    """
    Best-effort immediate stop for running mission (especially long writing steps).
    """
    try:
        state = get_graph_runner().steer_mission(
            task_id,
            "",
            intervention={
                "action": "pause",
                "force": True,
                "reason": "user requested stop",
            },
            priority=100,
            preempt=True,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    from app.services.client_display import build_steer_task_client_display
    from app.services.mission_steer import pending_steer_is_set

    queued = pending_steer_is_set(state.get("pending_user_message"))
    display = build_steer_task_client_display(state, queued=queued)
    return StopTaskResponse(
        task_id=task_id,
        status=str(state.get("status")),
        message="stop_queued",
        client_display=display,
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
    _principal: AuthPrincipal = Depends(require_task_access_dep),
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
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> StreamingResponse:
    """SSE stream for mission resume (progress, writing_delta, confirmation gates)."""
    def safe_generator():
        try:
            yield from get_graph_runner().stream_resume_mission(
                task_id, confirm=request.confirm
            )
        except KeyError as exc:
            payload = {"task_id": task_id, "detail": str(exc), "status": "NOT_FOUND"}
            yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except ValueError as exc:
            payload = {"task_id": task_id, "detail": str(exc), "status": "INVALID_RESUME"}
            yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception as exc:
            payload = {"task_id": task_id, "detail": str(exc), "status": "STREAM_ERROR"}
            yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        finally:
            done = {"task_id": task_id, "status": "FAILED"}
            yield f"event: done\ndata: {json.dumps(done, ensure_ascii=False)}\n\n"

    return StreamingResponse(safe_generator(), media_type="text/event-stream")


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
    from app.services.manuscript_checkpoint import enrich_agent_state_manuscript

    store_state = get_state_store().load(task_id, read_only=True)
    live_entry = get_live(task_id)
    if not store_state and not live_entry:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    if store_state:
        store_state = enrich_agent_state_manuscript(store_state)
    if live_entry is not None:
        from app.services.live_task_state import LiveTaskEntry

        live_entry = LiveTaskEntry(
            state=enrich_agent_state_manuscript(live_entry.state),
            updated_at=live_entry.updated_at,
            running=live_entry.running,
        )
    return build_task_state_debug_response(
        task_id=task_id,
        store_state=store_state,
        live_entry=live_entry,
        truncate=truncate,
    )


@router.get("/{task_id}/context-composition")
def get_task_context_composition(
    task_id: str,
    purpose: str = Query("reasoning", description="Context policy purpose"),
    _principal: AuthPrincipal = Depends(require_task_access_dep),
) -> dict[str, Any]:
    """
    Prompt composition debug view (ADR Context Governance §11.3).

    Builds a fresh envelope from current store+live state without invoking the LLM.
    """
    from app.services.manuscript_checkpoint import enrich_agent_state_manuscript
    from app.services.prompt_context_gateway import build_prompt_composition_for_state
    from app.services.state_debug_view import build_merged_debug_state

    store_state = get_state_store().load(task_id, read_only=True)
    live_entry = get_live(task_id)
    if not store_state and not live_entry:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    if store_state:
        store_state = enrich_agent_state_manuscript(store_state)
    if live_entry is not None:
        live_state = enrich_agent_state_manuscript(live_entry.state)
        state = (
            build_merged_debug_state(store_state, live_state)
            if store_state
            else live_state
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

    composition = build_prompt_composition_for_state(state, purpose=p)  # type: ignore[arg-type]
    session_meta = resolve_context_panel_meta(state)
    return {
        "task_id": task_id,
        "purpose": p,
        "composition": composition,
        "session": session_meta,
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
    from app.services.manuscript_checkpoint import enrich_agent_state_manuscript
    from app.services.prompt_context_gateway import manual_compress_context

    state = get_state_store().load(task_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    state = enrich_agent_state_manuscript(state)
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
    from app.services.manuscript_checkpoint import enrich_agent_state_manuscript

    from app.services.graph_run_registry import executor_active_for_state
    from app.services.mission_steer import pending_steer_is_set
    from app.services.mission_worker_lost import reconcile_worker_lost

    state = get_state_store().load(task_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    state = reconcile_worker_lost(state)
    state = enrich_agent_state_manuscript(state)
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
    if display_node == "mission_act" and str(state.get("status", "")) == "MISSION_RUNNING":
        manuscript = state.get("manuscript") or {}
        if manuscript.get("body_path"):
            display_node = "writing"
    mission_control = state.get("mission_control") if isinstance(state.get("mission_control"), dict) else {}
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
        pending_steer_queued=pending_steer_is_set(state.get("pending_user_message")),
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
