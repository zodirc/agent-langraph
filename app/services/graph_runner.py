"""任务编排层：HTTP/CLI 与 LangGraph 之间的唯一执行桥梁。

同步入口 start_task（POST /tasks）：
  task_api.create_task
    → prepare_session_turn（session_turn.py：新会话或续聊）
    → apply_skill_from_payload（skill_task_attach.py）
    → init_trace_context → state_store.save
    → 租户配额（可选）→ _run_with_slot(_invoke_graph_safe)
    → _maybe_pause_for_review（policy REVIEW → human_review_node）
    → _finalize_turn → 持久化与 skill_metrics

流式入口 stream_task（POST /tasks/stream）：
  task_api.stream_task → _stream_single 或 _stream_supervisor
  主线程 drain progress/trace/answer/thinking/writing 队列并输出 SSE
  工作线程 _run_graph 执行 stream_graph

图选择 _invoke_graph_safe(execution_mode)：
  supervisor → run_supervisor_graph
  默认 single → run_graph（app.runtime.graph）
  失败时 handle_invoke_failure 可 checkpoint_reset 后按同 mode 重试

Task orchestration: sole bridge from HTTP/CLI to LangGraph.

Sync start_task (POST /tasks):
  task_api.create_task → prepare_session_turn
  → apply_skill_from_payload → init_trace_context → state_store.save
  → tenant quota → _run_with_slot(_invoke_graph_safe) → _maybe_pause_for_review
  → _finalize_turn → persist and skill_metrics

Streaming stream_task (POST /tasks/stream):
  task_api.stream_task → _stream_single or _stream_supervisor; main thread drains
  side queues to SSE; worker thread runs stream_graph.

Graph selection _invoke_graph_safe(execution_mode):
  supervisor mode; default run_graph; handle_invoke_failure may reset
  checkpoint and retry.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any, Iterator, Optional

from app.config.settings import settings
from app.nodes.human_review_node import human_review_node
from app.runtime.graph import resume_graph, run_graph, stream_graph
from app.runtime.supervisor_graph import run_supervisor_graph, stream_supervisor_graph
from app.runtime.state import AgentState, TaskStatus, create_initial_state, merge_state
from app.services.audit_store import get_audit_store
from app.services.metrics_service import get_metrics_service
from app.services.conversation_context import persist_turn_draft_answer
from app.services.streaming_draft import (
    AnswerDraftPersistCtx,
    ThinkingDraftPersistCtx,
    absorb_thinking_delta,
    apply_streaming_draft,
    apply_streaming_thinking,
    clear_streaming_draft,
    force_persist_thinking_draft,
)
from app.services.chat_message_service import (
    StreamMessagePersistCtx,
    get_chat_message_service,
    user_text_from_state,
)
from app.services.chat_message_store import get_chat_message_store
from app.services.session_turn import finalize_turn_history, graph_thread_id, prepare_session_turn
from app.services.reasoning_trace import (
    answer_stream_enabled,
    report_boundary,
    thinking_stream_enabled,
    trace_after_node,
    trace_enabled,
)
from app.services.artifact_stream import artifact_stream_enabled
from app.services.stream_progress import (
    clear_stream_run_context,
    set_ack_handler,
    set_answer_handler,
    set_progress_handler,
    set_stream_run_context,
    set_thinking_handler,
    set_trace_handler,
    set_writing_handler,
)
from app.services.graph_run_registry import begin_graph_run, execution_run_meta, end_graph_run
from app.services.live_task_state import (
    clear_live,
    get_live,
    register_live,
    touch_live,
    touch_live_control,
)
from app.services.task_control import (
    clear_task_control,
    register_task_control,
    request_cancel,
    request_interrupt_stream,
    request_pause,
    snapshot_all_worker_controls,
    snapshot_task_control,
    snapshot_worker_control,
)
from app.services.execution_control import (
    CONTROL_PAUSE_REQUESTED,
    CONTROL_STREAMING_OUTPUT,
    build_control_response,
    effective_control_state,
    finalize_control_outcome,
    persist_control_request_to_state,
    record_control_event,
)
from app.services.state_store import get_state_store
from app.services.graph_execution_pool import (
    GraphExecutionRejected,
    get_graph_execution_pool,
)
from app.services.checkpoint_recovery import handle_invoke_failure
from app.services.tenant_context import get_tenant_id
from app.services.tenant_quota import (
    TenantQuotaExceeded,
    get_tenant_quota_store,
    require_quota,
)


def _begin_task_graph_run(state: AgentState) -> tuple[AgentState, str]:
    """Register in-process executor and task control; persist execution_run on state."""
    from app.services.foreground_execution import get_foreground_epoch, init_foreground_epoch_on_run
    from app.services.run_controller import RunController

    task_id = str(state["task_id"])
    run_id = begin_graph_run(task_id)
    state = init_foreground_epoch_on_run(state, run_id=run_id)
    register_task_control(task_id, run_id, foreground_epoch=get_foreground_epoch(state))
    state = RunController.begin_run(state, run_id)
    meta = dict(state.get("execution_run") or execution_run_meta(run_id))
    meta.setdefault("run_id", run_id)
    state = merge_state(state, execution_run=meta)
    return state, run_id


def _end_task_graph_run(task_id: str, run_id: str) -> None:
    """Drop executor registry, live snapshot, and task control when graph thread finishes."""
    end_graph_run(task_id, run_id)
    clear_live(task_id)
    clear_task_control(task_id)


_task_stream_workers: dict[str, threading.Thread] = {}
_task_stream_workers_lock = threading.Lock()


def _preempt_inflight_execution(task_id: str, *, reason: str = "new_stream") -> None:
    """Pause/cancel any in-process graph so a new SSE run does not inherit stale side effects."""
    from app.services.graph_run_registry import get_active_run_id

    tid = str(task_id)
    if get_active_run_id(tid):
        try:
            request_pause(tid, requested_by="graph_runner", reason=reason)
        except Exception:
            pass
    stored = get_state_store().load(tid)
    if not stored:
        return
    status = str(stored.get("status") or "")
    if status == TaskStatus.RUNNING.value or get_active_run_id(tid):
        from app.services.foreground_execution import trigger_foreground_preempt

        try:
            updated = trigger_foreground_preempt(tid, stored, reason=reason, tier=0)
            get_state_store().save(updated)
        except Exception:
            pass


def _abort_prior_stream_worker(task_id: str, *, reason: str = "new_stream") -> None:
    """Stop a prior daemon stream worker before registering a new one for the same task."""
    _preempt_inflight_execution(task_id, reason=reason)
    with _task_stream_workers_lock:
        prior = _task_stream_workers.get(str(task_id))
    if prior is not None and prior.is_alive() and prior is not threading.current_thread():
        prior.join(timeout=2.0)


def _register_stream_worker(task_id: str, worker: threading.Thread) -> None:
    with _task_stream_workers_lock:
        _task_stream_workers[str(task_id)] = worker


def _unregister_stream_worker(task_id: str, worker: threading.Thread) -> None:
    with _task_stream_workers_lock:
        if _task_stream_workers.get(str(task_id)) is worker:
            _task_stream_workers.pop(str(task_id), None)


def _accept_stream_side_event(
    item: dict[str, Any],
    *,
    run_id: str,
    foreground_epoch: int,
) -> bool:
    """Drop side-channel events from superseded graph runs (global handlers are shared)."""
    item_run = str(item.get("run_id") or "").strip()
    if item_run and item_run != str(run_id):
        return False
    item_epoch = int(item.get("foreground_epoch") or 0)
    if foreground_epoch > 0 and 0 < item_epoch < foreground_epoch:
        return False
    return True


def _sse_suppressed(task_id: str) -> bool:
    control = snapshot_task_control(str(task_id))
    return bool(control and control.stream_interrupted)


def _format_stream_exception(exc: BaseException) -> str:
    """Human-readable stream error; many library exceptions have an empty str()."""
    text = str(exc).strip()
    if text:
        return text
    name = type(exc).__name__
    if name == "GeneratorExit":
        return "stream closed (client disconnected; task was not cancelled)"
    return name


def _format_stream_event(event_type: str, payload: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _format_progress_event(
    task_id: str,
    message: str,
    *,
    started_at: float,
    phase: str = "working",
) -> str:
    elapsed = max(0, int(time.monotonic() - started_at))
    return _format_stream_event(
        "progress",
        {
            "task_id": task_id,
            "message": message,
            "elapsed_sec": elapsed,
            "phase": phase,
        },
    )


def _record_ui_telemetry(
    msg_ctx: StreamMessagePersistCtx | None,
    event_type: str,
    *,
    delta: str = "",
    meta: dict[str, Any] | None = None,
) -> None:
    if msg_ctx is None:
        return
    get_chat_message_service().record_stream_event(
        msg_ctx,
        event_type=event_type,
        delta=delta,
        meta=dict(meta or {}),
    )


def _emit_progress(
    task_id: str,
    message: str,
    *,
    started_at: float,
    phase: str = "working",
    msg_ctx: StreamMessagePersistCtx | None = None,
) -> Iterator[str]:
    elapsed = max(0, int(time.monotonic() - started_at))
    _record_ui_telemetry(
        msg_ctx,
        "ui_progress",
        delta=message,
        meta={"phase": phase, "elapsed_sec": elapsed},
    )
    yield _format_progress_event(task_id, message, started_at=started_at, phase=phase)


def _format_ack_event(task_id: str, payload: dict[str, Any]) -> str:
    body = {k: v for k, v in payload.items() if k not in {"node"}}
    body.setdefault("task_id", task_id)
    return _format_stream_event("ack", body)


def _format_trace_event(task_id: str, trace: dict[str, Any]) -> str:
    return _format_stream_event(
        "trace",
        {
            "task_id": task_id,
            "node": trace.get("node"),
            "phase": trace.get("phase"),
            "field": trace.get("field"),
            "text": trace.get("text"),
            "level": trace.get("level", "delta"),
        },
    )


def _drain_ack_queue(
    task_id: str,
    ack_q: queue.SimpleQueue[dict[str, Any]],
    *,
    run_id: str = "",
    foreground_epoch: int = 0,
    msg_ctx: StreamMessagePersistCtx | None = None,
) -> Iterator[str]:
    while True:
        try:
            item = ack_q.get_nowait()
        except queue.Empty:
            break
        if run_id and not _accept_stream_side_event(
            item, run_id=run_id, foreground_epoch=foreground_epoch
        ):
            continue
        if msg_ctx is not None:
            body = {k: v for k, v in item.items() if k != "node"}
            body.setdefault("task_id", task_id)
            _record_ui_telemetry(msg_ctx, "ui_ack", meta=body)
        yield _format_ack_event(task_id, item)


def _drain_trace_queue(
    task_id: str,
    trace_q: queue.SimpleQueue[dict[str, Any]],
    *,
    run_id: str = "",
    foreground_epoch: int = 0,
    msg_ctx: StreamMessagePersistCtx | None = None,
) -> Iterator[str]:
    svc = get_chat_message_service()
    while True:
        try:
            trace = trace_q.get_nowait()
        except queue.Empty:
            break
        if run_id and not _accept_stream_side_event(
            trace, run_id=run_id, foreground_epoch=foreground_epoch
        ):
            continue
        if msg_ctx is not None:
            svc.record_stream_event(
                msg_ctx,
                event_type="trace",
                delta=str(trace.get("text") or ""),
                meta={
                    "node": trace.get("node"),
                    "phase": trace.get("phase"),
                    "field": trace.get("field"),
                    "level": trace.get("level", "delta"),
                },
            )
        yield _format_trace_event(task_id, trace)


def _format_answer_delta_event(task_id: str, payload: dict[str, Any]) -> str:
    return _format_stream_event(
        "answer_delta",
        {
            "task_id": task_id,
            "node": payload.get("node"),
            "phase": payload.get("phase"),
            "field": payload.get("field", "summary"),
            "text": payload.get("text", ""),
        },
    )


def _drain_answer_queue(
    task_id: str,
    answer_q: queue.SimpleQueue[dict[str, Any]],
    *,
    draft_ctx: AnswerDraftPersistCtx | None = None,
    msg_ctx: StreamMessagePersistCtx | None = None,
) -> Iterator[str]:
    from app.services.streaming_draft import absorb_answer_delta

    svc = get_chat_message_service()
    while True:
        try:
            item = answer_q.get_nowait()
        except queue.Empty:
            break
        if draft_ctx is not None:
            absorb_answer_delta(draft_ctx, item)
        if msg_ctx is not None:
            svc.record_stream_event(
                msg_ctx,
                event_type="answer_delta",
                delta=str(item.get("text") or ""),
                meta={
                    "node": item.get("node"),
                    "phase": item.get("phase"),
                    "field": item.get("field", "summary"),
                },
            )
        yield _format_answer_delta_event(task_id, item)


def _format_thinking_delta_event(task_id: str, payload: dict[str, Any]) -> str:
    return _format_stream_event(
        "thinking_delta",
        {
            "task_id": task_id,
            "node": payload.get("node"),
            "phase": payload.get("phase"),
            "text": payload.get("text", ""),
        },
    )


def _persist_thinking_side_event(
    item: dict[str, Any],
    *,
    run_id: str,
    foreground_epoch: int,
    thinking_draft_ctx: ThinkingDraftPersistCtx | None,
    msg_ctx: StreamMessagePersistCtx | None,
) -> bool:
    """Persist thinking immediately (survives SSE client disconnect / page refresh)."""
    if run_id and not _accept_stream_side_event(
        item, run_id=run_id, foreground_epoch=foreground_epoch
    ):
        return False
    if thinking_draft_ctx is not None:
        absorb_thinking_delta(thinking_draft_ctx, item)
    if msg_ctx is not None:
        get_chat_message_service().record_stream_event(
            msg_ctx,
            event_type="thinking_delta",
            delta=str(item.get("text") or ""),
            meta={"node": item.get("node"), "phase": item.get("phase")},
        )
    return True


def _drain_thinking_queue(
    task_id: str,
    thinking_q: queue.SimpleQueue[dict[str, Any]],
    *,
    run_id: str = "",
    foreground_epoch: int = 0,
) -> Iterator[str]:
    """Drain thinking queue for SSE only (persistence happens in capture handler)."""
    while True:
        try:
            item = thinking_q.get_nowait()
        except queue.Empty:
            break
        if run_id and not _accept_stream_side_event(
            item, run_id=run_id, foreground_epoch=foreground_epoch
        ):
            continue
        yield _format_thinking_delta_event(task_id, item)


def _format_writing_delta_event(task_id: str, payload: dict[str, Any]) -> str:
    return _format_stream_event(
        "writing_delta",
        {
            "task_id": task_id,
            "node": payload.get("node"),
            "phase": payload.get("phase"),
            "filename": payload.get("filename", ""),
            "text": payload.get("text", ""),
            "reset": bool(payload.get("reset")),
        },
    )


def _drain_writing_queue(
    task_id: str,
    writing_q: queue.SimpleQueue[dict[str, Any]],
    *,
    run_id: str = "",
    foreground_epoch: int = 0,
    msg_ctx: StreamMessagePersistCtx | None = None,
) -> Iterator[str]:
    svc = get_chat_message_service()
    while True:
        try:
            item = writing_q.get_nowait()
        except queue.Empty:
            break
        if run_id and not _accept_stream_side_event(
            item, run_id=run_id, foreground_epoch=foreground_epoch
        ):
            continue
        if msg_ctx is not None:
            svc.record_stream_event(
                msg_ctx,
                event_type="writing_delta",
                delta=str(item.get("text") or ""),
                meta={
                    "node": item.get("node"),
                    "phase": item.get("phase"),
                    "filename": item.get("filename", ""),
                    "reset": bool(item.get("reset")),
                },
            )
        yield _format_writing_delta_event(task_id, item)


def _drain_sse_side_queues(
    task_id: str,
    trace_q: queue.SimpleQueue[dict[str, Any]],
    answer_q: queue.SimpleQueue[dict[str, Any]],
    thinking_q: queue.SimpleQueue[dict[str, Any]] | None = None,
    writing_q: queue.SimpleQueue[dict[str, Any]] | None = None,
    ack_q: queue.SimpleQueue[dict[str, Any]] | None = None,
    *,
    run_id: str = "",
    foreground_epoch: int = 0,
    draft_ctx: AnswerDraftPersistCtx | None = None,
    thinking_draft_ctx: ThinkingDraftPersistCtx | None = None,
    msg_ctx: StreamMessagePersistCtx | None = None,
) -> Iterator[str]:
    if _sse_suppressed(task_id):
        return
    if ack_q is not None:
        yield from _drain_ack_queue(
            task_id,
            ack_q,
            run_id=run_id,
            foreground_epoch=foreground_epoch,
            msg_ctx=msg_ctx,
        )
    if thinking_q is not None:
        yield from _drain_thinking_queue(
            task_id,
            thinking_q,
            run_id=run_id,
            foreground_epoch=foreground_epoch,
        )
    if writing_q is not None:
        yield from _drain_writing_queue(
            task_id,
            writing_q,
            run_id=run_id,
            foreground_epoch=foreground_epoch,
            msg_ctx=msg_ctx,
        )
    yield from _drain_answer_queue(task_id, answer_q, draft_ctx=draft_ctx, msg_ctx=msg_ctx)
    yield from _drain_trace_queue(
        task_id,
        trace_q,
        run_id=run_id,
        foreground_epoch=foreground_epoch,
        msg_ctx=msg_ctx,
    )


def _maybe_pause_for_review(state: AgentState) -> AgentState:
    if (
        state.get("policy_result") == "REVIEW"
        and not state.get("review_feedback")
        and state.get("status") != TaskStatus.WAITING_REVIEW.value
    ):
        return human_review_node(state)
    return state


def _emit_tool_preview(
    state: AgentState,
    *,
    msg_ctx: StreamMessagePersistCtx | None = None,
) -> Iterator[str]:
    from app.services.tool_result_helpers import format_tool_preview_snippet, tool_result_body

    svc = get_chat_message_service()
    for item in state.get("tool_results") or []:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "")
        result = tool_result_body(item)
        preview: dict[str, Any] = {"tool": tool, "status": item.get("status")}
        snippet = format_tool_preview_snippet(tool, result)
        if snippet:
            preview["snippet"] = snippet
        elif result.get("result") is not None:
            preview["snippet"] = str(result.get("result"))[:200]
        elif result.get("model_name"):
            preview["snippet"] = str(result.get("model_name"))
        elif result.get("path"):
            preview["snippet"] = str(result.get("path"))
        elif result.get("summary"):
            preview["snippet"] = str(result.get("summary"))[:200]
        if msg_ctx is not None:
            svc.record_stream_event(
                msg_ctx,
                event_type="tool_preview",
                delta=str(preview.get("snippet") or ""),
                meta=preview,
            )
        yield _format_stream_event(
            "tool_preview",
            {"task_id": state["task_id"], **preview},
        )


_MISSION_QUIET_NODES = frozenset()

_FOREGROUND_RUNTIME_NODES = frozenset(
    {
        "event_classification",
        "acknowledge",
        "interrupt_control",
        "incremental_planning",
    }
)
_BACKGROUND_RUNTIME_NODES = frozenset(
    {
        "retrieval",
        "tool_execution",
        "engineering_execution",
        "context_governance",
        "reasoning_or_writing",
        "verification",
        "policy",
        "output",
        "memory_writeback",
        "eval_capture",
    }
)


def _apply_execution_phase_status(state: AgentState, node_name: str) -> AgentState:
    """Stamp foreground/background phase for streaming observability (WP-1.5)."""
    from app.services.runtime_loops import emit_process_events, stamp_runtime_loops

    updated = stamp_runtime_loops(state, node_name)
    events: list[tuple[str, dict]] = []

    def _capture(kind: str, payload: dict) -> None:
        events.append((kind, payload))

    emit_process_events(node_name, updated, _capture)
    bg = dict(updated.get("background_status") or {})
    if events:
        bg["last_process_event"] = events[-1][1]
        updated = merge_state(updated, background_status=bg)
    return updated


_POST_DELIVERY_QUIET_NODES = frozenset({"memory_writeback", "eval_capture"})


def _should_emit_node_event(node_name: str, state: AgentState) -> bool:
    """Hide async post-delivery nodes from main chat; flow/debug panels keep history."""
    return node_name not in _POST_DELIVERY_QUIET_NODES


def _should_emit_early_delivered(state: AgentState) -> bool:
    """Emit ``delivered`` right after reasoning for answer-only narrate turns."""
    from app.runtime.state import TaskStatus
    from app.services.turn_kind import is_narrate_answer_turn_ready

    if str(state.get("status") or "") == TaskStatus.REJECTED.value:
        return False
    return is_narrate_answer_turn_ready(state)


def _format_delivered_event(state: AgentState) -> str:
    from app.services.confirmation.stream_display import client_final_answer

    return _format_stream_event(
        "delivered",
        {
            "task_id": state["task_id"],
            "session_id": state.get("session_id"),
            "session_turn": state.get("session_turn"),
            "status": state.get("status"),
            "final_answer": client_final_answer(state),
            "structured_output": state.get("structured_output"),
        },
    )


def _node_stream_payload(state: AgentState, node_name: str) -> dict[str, Any]:
    errors = list(state.get("errors") or [])
    status = str(state.get("status") or "")
    payload: dict[str, Any] = {
        "task_id": state["task_id"],
        "node": node_name,
        "status": status,
        "event_type": state.get("event_type"),
        "current_node": state.get("current_node"),
        "policy_result": state.get("policy_result"),
    }
    if errors and (
        status.endswith("FAILED") or status == TaskStatus.DEAD_LETTER.value
    ):
        payload["last_error"] = errors[-1]
    return payload


def _emit_worker_events(state: AgentState) -> Iterator[str]:
    for subtask_id, outcome in (state.get("worker_results") or {}).items():
        yield _format_stream_event(
            "worker",
            {
                "task_id": state["task_id"],
                "subtask_id": subtask_id,
                "domain": outcome.get("domain"),
                "status": outcome.get("status"),
                "summary": (outcome.get("summary") or "")[:200],
            },
        )


class GraphRunner:
    """
    图执行门面，封装 LangGraph 编译图的选择与同步/流式执行。

    对外 API：start_task、stream_task、pause_task、cancel_task、resume_task。

    Facade over compiled LangGraph graphs: start_task, stream_task,
    pause/cancel controls, resume_task (human review resume via resume_graph).
    """

    def _run_with_slot(self, runner_fn):
        pool = get_graph_execution_pool()
        with pool.acquire():
            return runner_fn()

    def _invoke_graph_safe(
        self,
        state: AgentState,
        *,
        thread: str,
        mode: str,
    ) -> AgentState:
        """Select graph by execution_mode.

        Unified core: only the single agent graph and the supervisor
        decomposition survive. Legacy mission/exploration modes are gone.
        """
        try:
            if mode == "supervisor":
                return run_supervisor_graph(state)
            return run_graph(state, thread_id=thread)
        except Exception as exc:
            handled = handle_invoke_failure(thread, exc, state=state)
            if handled and handled.get("status") == "checkpoint_reset":
                if mode == "supervisor":
                    return run_supervisor_graph(state)
                return run_graph(state, thread_id=thread)
            raise

    def _finalize_turn(self, state: AgentState) -> AgentState:
        from app.services.otel_export import finalize_trace_export
        from app.services.turn_guard import can_finalize_turn

        task_id = str(state["task_id"])
        control = snapshot_task_control(task_id)
        state = finalize_trace_export(state)
        state = finalize_turn_history(state)
        if control and (control.pause_requested or control.cancel_requested):
            state = finalize_control_outcome(state, control)
        elif state.get("interrupt_context"):
            ctx = state.get("interrupt_context") or {}
            if ctx.get("pause_requested") or ctx.get("cancel_requested"):
                state = finalize_control_outcome(state, None)
        allowed, reason = can_finalize_turn(state)
        if not allowed:
            from app.services.session_fsm import FSM_REPLANNING, get_fsm_state, set_fsm_state

            if get_fsm_state(state) == FSM_REPLANNING or reason.startswith("unexecuted_contract"):
                state = set_fsm_state(state, FSM_REPLANNING)
                if str(state.get("status") or "") == TaskStatus.COMPLETED.value:
                    state = merge_state(state, status=TaskStatus.PAUSED.value)
        from app.services.close_turn_async import close_turn_async

        close_turn_async(state)
        return state

    def start_task(
        self,
        *,
        user_id: str = "anonymous",
        task_type: str = "qa",
        input_payload: Optional[dict[str, Any]] = None,
        task_id: Optional[str] = None,
        execution_mode: str = "single",
        session_id: Optional[str] = None,
        new_session: bool = False,
    ) -> AgentState:
        """
        同步执行至图结束，返回终态 AgentState（含 COMPLETED、WAITING_REVIEW 等）。

        Run graph synchronously; return final AgentState.
        """
        payload = dict(input_payload or {})
        mode = execution_mode or payload.get("execution_mode", "single")
        session_key = session_id or payload.pop("session_id", None)
        new_sess = new_session or bool(payload.pop("new_session", False))
        state, created = prepare_session_turn(
            session_id=session_key,
            user_id=user_id,
            task_type=task_type,
            payload=payload,
            new_session=new_sess,
        )
        if task_id and not session_key:
            state = merge_state(state, task_id=task_id)
        if mode not in ("supervisor", "single"):
            mode = "single"
        state = merge_state(state, execution_mode=mode)
        # Promote _skill_* to state.skill_runtime_policy for planning/tool nodes
        if payload.get("skill_id") or payload.get("_skill_policy"):
            from app.services.skill_task_attach import apply_skill_from_payload

            state = apply_skill_from_payload(state, payload)
        thread = graph_thread_id(state)  # LangGraph checkpointer thread_id
        from app.services.engineering_trace import init_trace_context

        state = init_trace_context(state, thread_id=thread, tenant_id=get_tenant_id())
        get_state_store().save(state)
        if created:
            get_metrics_service().inc_task_created()
        exec_mode = (
            "supervisor"
            if mode == "supervisor" or task_type == "supervisor"
            else mode
        )
        tenant_id = get_tenant_id() or "default"
        quota_started = False
        if settings.MULTI_TENANT_ENABLED:
            require_quota(tenant_id, "tasks", 1)
            get_tenant_quota_store().task_started(tenant_id)
            get_metrics_service().inc_tenant_task(tenant_id)
            quota_started = True

        state, run_id = _begin_task_graph_run(state)
        get_state_store().save(state)

        def _execute() -> AgentState:
            return self._invoke_graph_safe(state, thread=thread, mode=exec_mode)

        try:
            final_state = self._run_with_slot(_execute)
        finally:
            _end_task_graph_run(str(state["task_id"]), run_id)
            if quota_started:
                get_tenant_quota_store().task_finished(tenant_id)
        final_state = _maybe_pause_for_review(final_state)
        final_state = self._finalize_turn(final_state)
        get_state_store().save(final_state)
        get_audit_store().append_events(final_state["task_id"], final_state.get("audit_log", []))
        from app.services.skill_metrics import record_skill_task_finished

        record_skill_task_finished(final_state)
        return final_state

    def stream_task(
        self,
        *,
        user_id: str = "anonymous",
        task_type: str = "qa",
        input_payload: Optional[dict[str, Any]] = None,
        task_id: Optional[str] = None,
        execution_mode: str = "single",
        session_id: Optional[str] = None,
        new_session: bool = False,
    ) -> Iterator[str]:
        """SSE-formatted stream of node execution events."""
        # Flush headers immediately so proxies/browsers do not treat long
        # prepare_session_turn / mission prep as a dead connection.
        yield _format_stream_event("stream_open", {"phase": "accepted", "message": "connected"})

        payload = dict(input_payload or {})
        mode = execution_mode or payload.get("execution_mode", "single")
        if mode == "supervisor" or task_type == "supervisor":
            yield from self._stream_supervisor(
                user_id=user_id,
                task_type=task_type,
                input_payload=payload,
                task_id=task_id,
            )
            return

        session_key = session_id or payload.pop("session_id", None)
        new_sess = new_session or bool(payload.pop("new_session", False))
        state, created = prepare_session_turn(
            session_id=session_key,
            user_id=user_id,
            task_type=task_type,
            payload=payload,
            new_session=new_sess,
        )
        if task_id and not session_key:
            state = merge_state(state, task_id=task_id)
        state_payload = dict(state.get("input_payload") or {})
        from app.services.session_fsm import is_fsm_replanning, stamp_session_mode, sync_fsm_state

        state = sync_fsm_state(state)
        state_payload = stamp_session_mode(dict(state.get("input_payload") or {}))
        state = merge_state(state, input_payload=state_payload)
        state = merge_state(state, execution_mode="single")
        from app.services.engineering_trace import init_trace_context

        state = init_trace_context(
            state,
            thread_id=graph_thread_id(state),
            tenant_id=get_tenant_id(),
        )
        get_state_store().save(state)
        yield from self._stream_single(state, created=created)

    def _stream_single(self, state: AgentState, *, created: bool = True) -> Iterator[str]:
        """
        单任务 SSE：主线程轮询 node_q 与侧信道队列，工作线程跑 stream_graph。

        侧信道：progress_q、trace_q、answer_q、thinking_q、writing_q。

        SSE pump: caller thread drains queues; worker runs stream_graph.
        """
        yield _format_stream_event(
            "task_created",
            {
                "task_id": state["task_id"],
                "session_id": state["session_id"],
                "session_turn": state.get("session_turn"),
                "status": state["status"],
                "message": "Task created" if created else "Session turn continued",
                "continued": not created,
            },
        )
        latest = state
        interrupted_for_review = False
        started_at = time.monotonic()
        task_id = state["task_id"]
        _abort_prior_stream_worker(str(task_id), reason="stream_single")
        state, run_id = _begin_task_graph_run(state)
        from app.services.foreground_execution import get_foreground_epoch

        stream_fg_epoch = get_foreground_epoch(state)
        get_state_store().save(state)
        register_live(state, run_id=run_id)
        draft_ctx = AnswerDraftPersistCtx(state=state)
        thinking_draft_ctx = ThinkingDraftPersistCtx(state=state)
        payload_lp = state.get("input_payload") or {}
        client_message_id = str(payload_lp.get("client_message_id") or "") or None
        msg_svc = get_chat_message_service()
        user_message_id = msg_svc.record_user_message(
            state,
            text=user_text_from_state(state),
            client_message_id=client_message_id,
        )
        msg_ctx = StreamMessagePersistCtx(
            task_id=str(task_id),
            session_id=str(state.get("session_id") or task_id),
            session_turn=int(state.get("session_turn") or 0),
            user_message_id=user_message_id,
            next_seq=get_chat_message_store().next_event_seq(str(task_id)),
        )
        _record_ui_telemetry(
            msg_ctx,
            "ui_task_created",
            meta={
                "task_id": str(task_id),
                "session_id": str(state.get("session_id") or task_id),
                "session_turn": state.get("session_turn"),
                "status": state["status"],
                "message": "Task created" if created else "Session turn continued",
                "continued": not created,
            },
        )
        progress_q: queue.SimpleQueue[str] = queue.SimpleQueue()
        trace_q: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        answer_q: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        thinking_q: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        writing_q: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        ack_q: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        node_q: queue.SimpleQueue[tuple[str, AgentState] | None] = queue.SimpleQueue()
        stream_error: list[BaseException | None] = [None]
        last_event_at = started_at
        delivered_emitted = False

        def _capture_progress(message: str) -> None:
            progress_q.put(message)

        def _capture_trace(trace: dict[str, Any]) -> None:
            trace_q.put(trace)

        def _capture_answer(delta: dict[str, Any]) -> None:
            answer_q.put(delta)

        def _capture_thinking(delta: dict[str, Any]) -> None:
            _persist_thinking_side_event(
                delta,
                run_id=run_id,
                foreground_epoch=stream_fg_epoch,
                thinking_draft_ctx=thinking_draft_ctx,
                msg_ctx=msg_ctx,
            )
            thinking_q.put(delta)

        def _capture_writing(delta: dict[str, Any]) -> None:
            writing_q.put(delta)

        def _capture_ack(payload: dict[str, Any]) -> None:
            ack_q.put(payload)

        exec_mode = str(state.get("execution_mode", "")).lower()

        def _run_graph() -> None:
            set_stream_run_context(run_id=run_id, foreground_epoch=stream_fg_epoch)
            try:
                for node_name, snapshot in stream_graph(
                    state, thread_id=graph_thread_id(state)
                ):
                    node_q.put((node_name, snapshot))
            except GeneratorExit:
                raise
            except BaseException as exc:
                stream_error[0] = exc
            finally:
                clear_stream_run_context()
                node_q.put(None)
                _end_task_graph_run(task_id, run_id)

        yield from _emit_progress(
            task_id,
            "任务已开始，Agent 正在处理…",
            started_at=started_at,
            phase="started",
            msg_ctx=msg_ctx,
        )

        set_progress_handler(_capture_progress)
        set_trace_handler(_capture_trace)
        set_ack_handler(_capture_ack)
        set_answer_handler(_capture_answer if answer_stream_enabled() else None)
        set_thinking_handler(_capture_thinking if thinking_stream_enabled() else None)
        set_writing_handler(_capture_writing if artifact_stream_enabled() else None)
        worker = threading.Thread(target=_run_graph, daemon=True)
        _register_stream_worker(str(task_id), worker)
        worker.start()
        try:
            while worker.is_alive() or not node_q.empty():
                yield from _drain_sse_side_queues(
                    task_id,
                    trace_q,
                    answer_q,
                    thinking_q,
                    writing_q,
                    ack_q,
                    run_id=run_id,
                    foreground_epoch=stream_fg_epoch,
                    draft_ctx=draft_ctx,
                    thinking_draft_ctx=thinking_draft_ctx,
                    msg_ctx=msg_ctx,
                )
                while True:
                    try:
                        msg = progress_q.get_nowait()
                    except queue.Empty:
                        break
                    yield from _emit_progress(
                        task_id,
                        msg,
                        started_at=started_at,
                        phase="working",
                        msg_ctx=msg_ctx,
                    )
                    last_event_at = time.monotonic()

                now = time.monotonic()
                control = snapshot_task_control(task_id)
                if control and (control.pause_requested or control.cancel_requested):
                    live_entry = get_live(task_id)
                    touch_live_control(
                        task_id,
                        control_state=effective_control_state(control),
                        active_step_id=live_entry.active_step_id if live_entry else None,
                    )

                if now - last_event_at >= 8.0 and worker.is_alive() and not delivered_emitted:
                    yield from _emit_progress(
                        task_id,
                        "仍在处理中…",
                        started_at=started_at,
                        phase="heartbeat",
                        msg_ctx=msg_ctx,
                    )
                    last_event_at = now

                try:
                    item = node_q.get(timeout=0.08)
                except queue.Empty:
                    yield from _drain_sse_side_queues(
                        task_id,
                        trace_q,
                        answer_q,
                        thinking_q,
                        writing_q,
                        ack_q,
                        run_id=run_id,
                        foreground_epoch=stream_fg_epoch,
                        draft_ctx=draft_ctx,
                        thinking_draft_ctx=thinking_draft_ctx,
                        msg_ctx=msg_ctx,
                    )
                    continue
                if item is None:
                    break

                yield from _drain_sse_side_queues(
                    task_id,
                    trace_q,
                    answer_q,
                    thinking_q,
                    writing_q,
                    ack_q,
                    run_id=run_id,
                    foreground_epoch=stream_fg_epoch,
                    draft_ctx=draft_ctx,
                    thinking_draft_ctx=thinking_draft_ctx,
                    msg_ctx=msg_ctx,
                )
                while True:
                    try:
                        msg = progress_q.get_nowait()
                    except queue.Empty:
                        break
                    yield from _emit_progress(
                        task_id,
                        msg,
                        started_at=started_at,
                        phase="working",
                        msg_ctx=msg_ctx,
                    )
                    last_event_at = time.monotonic()

                node_name, snapshot = item
                latest = _apply_execution_phase_status(snapshot, node_name)
                if draft_ctx.accumulated:
                    latest = apply_streaming_draft(latest, draft_ctx.accumulated)
                if thinking_draft_ctx.accumulated:
                    latest = apply_streaming_thinking(latest, thinking_draft_ctx.accumulated)
                if node_name == "reasoning" and thinking_draft_ctx.accumulated:
                    latest, thinking_draft_ctx = force_persist_thinking_draft(
                        thinking_draft_ctx,
                        latest,
                        completed=True,
                    )
                    if msg_ctx is not None:
                        get_chat_message_service().sync_thinking_text(
                            msg_ctx,
                            thinking_draft_ctx.accumulated,
                            persist_snapshot_event=True,
                        )
                draft_ctx.state = latest
                thinking_draft_ctx.state = latest
                touch_live(latest)
                if trace_enabled():
                    trace_after_node(node_name, latest)
                from app.services.engineering_trace import record_node_span

                span_status = (
                    "error"
                    if str(latest.get("status", "")).endswith("FAILED")
                    or latest.get("status") == TaskStatus.DEAD_LETTER.value
                    else "ok"
                )
                latest = record_node_span(latest, node_name, status=span_status)
                if settings.STREAM_SAVE_EVERY_NODE:
                    get_state_store().save(latest)
                if _should_emit_node_event(node_name, latest):
                    node_payload = _node_stream_payload(latest, node_name)
                    _record_ui_telemetry(msg_ctx, "ui_node", meta=node_payload)
                    yield _format_stream_event("node", node_payload)
                if (
                    not delivered_emitted
                    and node_name == "reasoning_or_writing"
                    and _should_emit_early_delivered(latest)
                ):
                    delivered_emitted = True
                    yield _format_delivered_event(latest)
                if node_name == "output" and not delivered_emitted:
                    delivered_emitted = True
                    yield _format_delivered_event(latest)
                if node_name == "acknowledge":
                    fg = latest.get("foreground_status") or {}
                    last_ack = fg.get("last_ack") if isinstance(fg.get("last_ack"), dict) else {}
                    if last_ack:
                        ack_body = {**last_ack, "task_id": task_id}
                        _record_ui_telemetry(msg_ctx, "ui_ack", meta=ack_body)
                        yield _format_ack_event(task_id, ack_body)
                if node_name == "incremental_planning" and latest.get("plan"):
                    plan_payload = {
                        "task_id": latest["task_id"],
                        "plan": latest.get("plan", []),
                        "selected_tools": latest.get("selected_tools", []),
                    }
                    _record_ui_telemetry(msg_ctx, "ui_plan", meta=plan_payload)
                    yield _format_stream_event("plan", plan_payload)
                if node_name == "tool_execution":
                    yield from _emit_tool_preview(latest, msg_ctx=msg_ctx)
                if (
                    node_name in ("reasoning", "reasoning_or_writing")
                    and latest.get("reasoning_result")
                    and not answer_stream_enabled()
                ):
                    rr = latest["reasoning_result"]
                    structured = rr.get("structured") if isinstance(rr.get("structured"), dict) else {}
                    from app.services.answer_compose import compose_user_answer_preview

                    summary = compose_user_answer_preview(
                        str(rr.get("summary") or ""), structured
                    )
                    if summary:
                        if msg_ctx is not None:
                            get_chat_message_service().record_stream_event(
                                msg_ctx,
                                event_type="answer_preview",
                                delta=summary,
                                meta={"node": node_name},
                            )
                        yield _format_stream_event(
                            "answer_preview",
                            {"task_id": latest["task_id"], "text": summary},
                        )
                if latest.get("policy_result") == "REVIEW" and node_name == "policy":
                    interrupted_for_review = True
                if node_name in ("verification", "rejected"):
                    from app.services.confirmation.stream_display import (
                        rejection_detail,
                        streamed_answer_revoked,
                    )

                    if streamed_answer_revoked(latest):
                        if msg_ctx is not None:
                            get_chat_message_service().record_stream_event(
                                msg_ctx,
                                event_type="answer_revoked",
                                delta=rejection_detail(latest),
                                meta={"reason": rejection_detail(latest)},
                            )
                        yield _format_stream_event(
                            "answer_revoked",
                            {
                                "task_id": task_id,
                                "status": latest.get("status"),
                                "reason": rejection_detail(latest),
                            },
                        )

            yield from _drain_sse_side_queues(
                task_id,
                trace_q,
                answer_q,
                thinking_q,
                writing_q,
                ack_q,
                run_id=run_id,
                foreground_epoch=stream_fg_epoch,
                draft_ctx=draft_ctx,
                thinking_draft_ctx=thinking_draft_ctx,
                msg_ctx=msg_ctx,
            )
            while True:
                try:
                    msg = progress_q.get_nowait()
                except queue.Empty:
                    break
                yield from _emit_progress(
                    task_id,
                    msg,
                    started_at=started_at,
                    phase="working",
                    msg_ctx=msg_ctx,
                )

            if draft_ctx.accumulated:
                latest = apply_streaming_draft(latest, draft_ctx.accumulated)
            if thinking_draft_ctx.accumulated:
                latest = apply_streaming_thinking(latest, thinking_draft_ctx.accumulated)
                latest, thinking_draft_ctx = force_persist_thinking_draft(
                    thinking_draft_ctx,
                    latest,
                    completed=True,
                )
            if draft_ctx.accumulated or thinking_draft_ctx.accumulated:
                latest = get_state_store().save(latest)
                touch_live(latest)
                draft_ctx.state = latest
                thinking_draft_ctx.state = latest

            if stream_error[0] is not None:
                yield from self._stream_error(latest, stream_error[0])
                return
        finally:
            set_progress_handler(None)
            set_trace_handler(None)
            set_ack_handler(None)
            set_answer_handler(None)
            set_thinking_handler(None)
            set_writing_handler(None)
            _unregister_stream_worker(str(task_id), worker)
            if worker is not threading.current_thread():
                worker.join()
            _end_task_graph_run(task_id, run_id)

        yield from self._stream_finalize(latest, interrupted_for_review, msg_ctx=msg_ctx)

    def _stream_supervisor(
        self,
        *,
        user_id: str,
        task_type: str,
        input_payload: dict[str, Any],
        task_id: Optional[str],
    ) -> Iterator[str]:
        """
        Supervisor 流式：无 session_turn，在调用线程同步 stream_supervisor_graph。

        Supervisor SSE without session_turn; sync stream_supervisor_graph on caller thread.
        """
        state = create_initial_state(
            task_id=task_id,
            user_id=user_id,
            task_type=task_type,
            input_payload=input_payload,
        )
        state = merge_state(state, execution_mode="supervisor")
        get_state_store().save(state)
        yield _format_stream_event(
            "task_created",
            {
                "task_id": state["task_id"],
                "status": state["status"],
                "message": "Supervisor task created",
                "execution_mode": "supervisor",
            },
        )

        latest = state
        interrupted_for_review = False
        task_id = state["task_id"]
        state, run_id = _begin_task_graph_run(state)
        get_state_store().save(state)
        register_live(state, run_id=run_id)
        try:
            for node_name, snapshot in stream_supervisor_graph(state):
                latest = snapshot
                touch_live(latest)
                get_state_store().save(latest)
                if node_name == "supervisor_decompose":
                    yield _format_stream_event(
                        "subtasks",
                        {
                            "task_id": latest["task_id"],
                            "subtasks": latest.get("subtasks", []),
                            "count": len(latest.get("subtasks") or []),
                        },
                    )
                if node_name == "supervisor_worker":
                    yield from _emit_worker_events(latest)
                yield _format_stream_event(
                    "node",
                    _node_stream_payload(latest, node_name),
                )
                if latest.get("policy_result") == "REVIEW" and node_name == "policy":
                    interrupted_for_review = True
        except Exception as exc:
            yield from self._stream_error(latest, exc)
            return
        finally:
            _end_task_graph_run(task_id, run_id)

        yield from self._stream_finalize(latest, interrupted_for_review)

    def _stream_error(self, latest: AgentState, exc: BaseException) -> Iterator[str]:
        from app.services.execution_control import (
            CancelRequested,
            PauseRequested,
            StreamInterrupted,
            finalize_control_outcome,
            handle_control_exception,
        )

        if isinstance(exc, GeneratorExit):
            try:
                from app.services.metrics_service import get_metrics_service

                get_metrics_service().inc_disconnect_without_cancel()
            except Exception:
                pass
            detail = _format_stream_exception(exc)
            yield _format_stream_event(
                "stream_closed",
                {"task_id": latest["task_id"], "detail": detail, "cancelled": False},
            )
            yield from self._stream_finalize(latest, interrupted_for_review=False)
            return

        handled = handle_control_exception(latest, exc)
        if handled is not None:
            control = snapshot_task_control(str(latest["task_id"]))
            latest = finalize_control_outcome(handled, control)
            get_state_store().save(latest)
            yield _format_stream_event(
                "task_control",
                {
                    "task_id": latest["task_id"],
                    "status": latest.get("status"),
                    "control_event": type(exc).__name__,
                },
            )
            yield from self._stream_finalize(latest, interrupted_for_review=False)
            return

        detail = _format_stream_exception(exc)
        latest = merge_state(
            latest,
            errors=list(latest.get("errors", [])) + [detail],
            status=TaskStatus.FAILED.value,
        )
        get_state_store().save(latest)
        yield _format_stream_event("error", {"task_id": latest["task_id"], "detail": detail})
        yield _format_stream_event(
            "done",
            {"task_id": latest["task_id"], "status": latest["status"], "final_answer": None},
        )

    def _stream_finalize(
        self,
        latest: AgentState,
        interrupted_for_review: bool,
        *,
        msg_ctx: StreamMessagePersistCtx | None = None,
    ) -> Iterator[str]:
        from app.services.confirmation.stream_display import client_final_answer

        if interrupted_for_review and latest.get("status") != TaskStatus.WAITING_REVIEW.value:
            latest = persist_turn_draft_answer(latest)
            latest = clear_streaming_draft(latest)
            latest = human_review_node(latest)
            get_state_store().save(latest)
            if msg_ctx is not None:
                get_chat_message_service().finalize_assistant_message(
                    msg_ctx,
                    status=get_chat_message_service().status_for_turn_end(latest),
                    content=client_final_answer(latest) or msg_ctx.answer_text,
                )
            yield _format_stream_event(
                "review_required",
                {
                    "task_id": latest["task_id"],
                    "status": latest["status"],
                    "message": "Waiting for human review. Use POST /reviews to continue.",
                },
            )
        latest = self._finalize_turn(latest)
        latest = get_state_store().save(latest)
        if msg_ctx is not None and not (
            interrupted_for_review and latest.get("status") == TaskStatus.WAITING_REVIEW.value
        ):
            thinking_text = str(latest.get("streaming_thinking_text") or "").strip()
            if thinking_text:
                get_chat_message_service().sync_thinking_text(
                    msg_ctx,
                    thinking_text,
                    persist_snapshot_event=True,
                )
            get_chat_message_service().finalize_assistant_message(
                msg_ctx,
                status=get_chat_message_service().status_for_turn_end(latest),
                content=client_final_answer(latest) or msg_ctx.answer_text,
                thinking_text=thinking_text or None,
            )
        get_audit_store().append_events(latest["task_id"], latest.get("audit_log", []))
        if latest.get("status") == TaskStatus.COMPLETED.value:
            get_metrics_service().inc_task_completed(
                retry_count=int(latest.get("retry_count") or 0)
            )
        elif latest.get("status") in (
            TaskStatus.FAILED.value,
            TaskStatus.DEAD_LETTER.value,
            TaskStatus.REJECTED.value,
        ):
            get_metrics_service().inc_task_failed()
        from app.services.skill_metrics import record_skill_task_finished

        record_skill_task_finished(latest)
        if latest.get("status") == TaskStatus.PAUSED.value:
            from app.services.client_display import build_mission_paused_payload

            paused_body = build_mission_paused_payload(latest)
            yield _format_stream_event("mission_paused", paused_body)
        done_payload_lp = latest.get("input_payload") or {}
        from app.services.confirmation.stream_display import (
            build_gate_sse_fields,
            client_final_answer,
            rejection_detail,
            streamed_answer_revoked,
        )

        revoked = streamed_answer_revoked(latest)
        done_body: dict[str, Any] = {
            "task_id": latest["task_id"],
            "session_id": latest.get("session_id"),
            "session_turn": latest.get("session_turn"),
            "status": latest.get("status"),
            "current_node": latest.get("current_node"),
            "final_answer": client_final_answer(latest),
            "structured_output": latest.get("structured_output"),
            "review_required": latest.get("review_required"),
            "worker_results": latest.get("worker_results"),
            "subtasks": latest.get("subtasks"),
            "steer_review_only": False,
            **build_gate_sse_fields(latest["task_id"], latest),
        }
        if revoked:
            done_body["answer_revoked"] = True
            done_body["rejection_reason"] = rejection_detail(latest)
        if msg_ctx is not None:
            done_body["assistant_message_id"] = msg_ctx.assistant_message_id
            done_body["last_event_seq"] = max(0, msg_ctx.next_seq - 1)
        yield _format_stream_event("done", done_body)

    def _apply_control_request(
        self,
        task_id: str,
        *,
        control_action: str,
        mutator,
        requested_by: str = "web",
        reason: str = "user_requested",
        worker_id: str | None = None,
    ) -> dict[str, Any]:
        stored = get_state_store().load(task_id)
        if not stored:
            raise KeyError(f"Task not found: {task_id}")

        if worker_id:
            control = mutator(
                task_id,
                worker_id=worker_id,
                requested_by=requested_by,
                reason=reason,
            )
            worker_control = snapshot_worker_control(task_id, worker_id)
            task_control = snapshot_task_control(task_id)
        else:
            control = mutator(task_id, requested_by=requested_by, reason=reason)
            worker_control = None
            task_control = control
        live = get_live(task_id)
        active_step_id = live.active_step_id if live else None

        event_name = control_action.replace("-", "_")
        if control is not None:
            eff = effective_control_state(task_control)
            if worker_control and worker_control.cancel_requested:
                from app.services.execution_control import CONTROL_CANCEL_REQUESTED

                eff = CONTROL_CANCEL_REQUESTED
            elif worker_control and worker_control.pause_requested:
                from app.services.execution_control import CONTROL_PAUSE_REQUESTED

                eff = CONTROL_PAUSE_REQUESTED
            touch_live_control(task_id, control_state=eff, active_step_id=active_step_id)
            updated = record_control_event(
                stored,
                f"task_{event_name}_requested",
                detail={
                    "requested_by": requested_by,
                    "reason": reason,
                    "worker_id": worker_id,
                },
            )
        else:
            from app.services.execution_control import (
                CONTROL_CANCEL_REQUESTED,
                CONTROL_PAUSE_REQUESTED,
            )

            eff = CONTROL_STREAMING_OUTPUT
            if control_action == "pause_task":
                eff = CONTROL_PAUSE_REQUESTED
            elif control_action == "cancel_task":
                eff = CONTROL_CANCEL_REQUESTED
            updated = persist_control_request_to_state(
                stored,
                pause=control_action == "pause_task",
                cancel=control_action == "cancel_task",
                stream_interrupt=control_action == "interrupt_stream",
                requested_by=requested_by,
                reason=reason,
                worker_id=worker_id,
            )
            updated = record_control_event(
                updated,
                f"task_{event_name}_requested",
                detail={
                    "requested_by": requested_by,
                    "reason": reason,
                    "deferred": True,
                    "worker_id": worker_id,
                },
            )

        if control_action == "pause_task":
            pass  # Unified control: task_control registry only (no steer queue)

        get_state_store().save(updated)
        get_audit_store().append_events(task_id, updated.get("audit_log", []))

        from app.services.graph_run_registry import is_graph_run_active
        from app.services.streaming_draft import clear_streaming_draft

        if control_action in ("pause_task", "cancel_task") and not is_graph_run_active(task_id):
            stored_idle = get_state_store().load(task_id) or updated
            idle_control = snapshot_task_control(task_id)
            finalized = finalize_control_outcome(stored_idle, idle_control)
            finalized = clear_streaming_draft(finalized)
            finalized = merge_state(finalized, execution_run=None)
            get_state_store().save(finalized)
            clear_live(task_id)

        return build_control_response(
            task_id,
            control_action=control_action,
            accepted=True,
            control=task_control,
            worker_control=worker_control,
            active_step_id=active_step_id,
            worker_id=worker_id,
        )

    def interrupt_task_stream(
        self,
        task_id: str,
        *,
        requested_by: str = "web",
        reason: str = "user_requested",
    ) -> dict[str, Any]:
        return self._apply_control_request(
            task_id,
            control_action="interrupt_stream",
            mutator=request_interrupt_stream,
            requested_by=requested_by,
            reason=reason,
        )

    def pause_task(
        self,
        task_id: str,
        *,
        requested_by: str = "web",
        reason: str = "user_requested",
        worker_id: str | None = None,
    ) -> dict[str, Any]:
        return self._apply_control_request(
            task_id,
            control_action="pause_task",
            mutator=request_pause,
            requested_by=requested_by,
            reason=reason,
            worker_id=worker_id,
        )

    def cancel_task(
        self,
        task_id: str,
        *,
        requested_by: str = "web",
        reason: str = "user_requested",
        worker_id: str | None = None,
    ) -> dict[str, Any]:
        return self._apply_control_request(
            task_id,
            control_action="cancel_task",
            mutator=request_cancel,
            requested_by=requested_by,
            reason=reason,
            worker_id=worker_id,
        )

    def get_task_control_snapshot(self, task_id: str) -> dict[str, Any]:
        stored = get_state_store().load(task_id)
        if not stored:
            raise KeyError(f"Task not found: {task_id}")
        control = snapshot_task_control(task_id)
        live = get_live(task_id)
        ctx = stored.get("interrupt_context") or {}
        active = ctx.get("active_step") if isinstance(ctx.get("active_step"), dict) else {}
        return {
            "task_id": task_id,
            "running": bool(live and live.running),
            "run_id": (live.run_id if live else None) or (control.run_id if control else None),
            "effective_state": (
                effective_control_state(control) if control else ctx.get("control_state", "IDLE")
            ),
            "stream_interrupted": bool(control and control.stream_interrupted),
            "pause_requested": bool(
                (control and control.pause_requested) or ctx.get("pause_requested")
            ),
            "cancel_requested": bool(
                (control and control.cancel_requested) or ctx.get("cancel_requested")
            ),
            "active_step_id": (live.active_step_id if live else None)
            or active.get("step_id"),
            "last_committed_step": ctx.get("last_committed_step"),
            "interrupt_context": ctx,
            "worker_controls": {
                wid: {
                    "worker_id": wc.worker_id,
                    "pause_requested": wc.pause_requested,
                    "cancel_requested": wc.cancel_requested,
                    "requested_at": wc.requested_at,
                    "reason": wc.reason,
                }
                for wid, wc in snapshot_all_worker_controls(task_id).items()
            },
        }

    def submit_review(
        self,
        task_id: str,
        action: str,
        comment: str = "",
    ) -> AgentState:
        stored = get_state_store().load(task_id)
        if not stored:
            raise KeyError(f"Task not found: {task_id}")
        if stored.get("status") != TaskStatus.WAITING_REVIEW.value:
            raise ValueError(f"Task {task_id} is not waiting for review")

        resumed = merge_state(
            stored,
            review_feedback={"action": action, "comment": comment},
        )
        if stored.get("execution_mode") == "supervisor":
            from app.runtime.supervisor_graph import resume_supervisor_graph

            final_state = resume_supervisor_graph(resumed)
        else:
            final_state = resume_graph(resumed, thread_id=graph_thread_id(resumed))
        final_state = self._finalize_turn(final_state)
        get_state_store().save(final_state)
        get_audit_store().append_events(task_id, final_state.get("audit_log", []))
        return final_state


_runner: GraphRunner | None = None


def get_graph_runner() -> GraphRunner:
    global _runner
    if _runner is None:
        _runner = GraphRunner()
    return _runner
