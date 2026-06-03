"""任务编排层：HTTP/CLI 与 LangGraph 之间的唯一执行桥梁。

同步入口 start_task（POST /tasks）：
  task_api.create_task
    → prepare_session_turn（session_turn.py：新会话或续聊）
    → _prepare_mission_for_turn（mission 初始化或 steer 续轮）
    → apply_skill_from_payload（skill_task_attach.py）
    → init_trace_context → state_store.save
    → 租户配额（可选）→ _run_with_slot(_invoke_graph_safe)
    → _maybe_pause_for_review（policy REVIEW → human_review_node）
    → _finalize_turn → 持久化与 skill_metrics

流式入口 stream_task（POST /tasks/stream）：
  task_api.stream_task → _stream_single 或 _stream_supervisor
  主线程 drain progress/trace/answer/thinking/writing 队列并输出 SSE
  工作线程 _run_graph 执行 stream_graph 或 stream_mission_graph（含 handoff）

图选择 _invoke_graph_safe(execution_mode)：
  supervisor → run_supervisor_graph；exploration → run_exploration_graph
  mission → run_mission_graph
  single 且 enable_planning_mission_handoff：先 stream_graph 至 planning，再切 mission_graph
  默认 single → run_graph（app.runtime.graph）
  失败时 handle_invoke_failure 可 checkpoint_reset 后按同 mode 重试

Mission steer（POST /tasks/{id}/steer）：
  steer_mission → mission_steer.queue_steer_message
  MISSION_PAUSED/REASONED 立即 apply_steer_message；RUNNING 写入 pending_user_message

Task orchestration: sole bridge from HTTP/CLI to LangGraph.

Sync start_task (POST /tasks):
  task_api.create_task → prepare_session_turn → _prepare_mission_for_turn
  → apply_skill_from_payload → init_trace_context → state_store.save
  → tenant quota → _run_with_slot(_invoke_graph_safe) → _maybe_pause_for_review
  → _finalize_turn → persist and skill_metrics

Streaming stream_task (POST /tasks/stream):
  task_api.stream_task → _stream_single or _stream_supervisor; main thread drains
  side queues to SSE; worker thread runs stream_graph or stream_mission_graph.

Graph selection _invoke_graph_safe(execution_mode):
  supervisor/exploration/mission modes; optional planning→mission handoff on single;
  default run_graph; handle_invoke_failure may reset checkpoint and retry.

Mission steer: steer_mission → queue_steer_message; paused applies immediately,
running queues pending_user_message for consume_pending_steer at step boundary.
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
from app.runtime.mission_graph import run_mission_graph, stream_mission_graph
from app.runtime.exploration_graph import run_exploration_graph, stream_exploration_graph
from app.runtime.supervisor_graph import run_supervisor_graph, stream_supervisor_graph
from app.services.mission_schema import should_use_mission_runtime
from app.services.mission_service import init_mission_state
from app.runtime.state import AgentState, TaskStatus, create_initial_state, merge_state
from app.services.audit_store import get_audit_store
from app.services.metrics_service import get_metrics_service
from app.services.conversation_context import persist_turn_draft_answer
from app.services.session_turn import finalize_turn_history, graph_thread_id, prepare_session_turn
from app.services.reasoning_trace import (
    answer_stream_enabled,
    report_boundary,
    thinking_stream_enabled,
    trace_after_node,
    trace_enabled,
)
from app.services.writing_stream import writing_stream_enabled
from app.services.stream_progress import (
    set_answer_handler,
    set_progress_handler,
    set_thinking_handler,
    set_trace_handler,
    set_writing_handler,
)
from app.services.graph_run_registry import begin_graph_run, execution_run_meta, end_graph_run
from app.services.live_task_state import clear_live, register_live, touch_live
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


def _prepare_mission_for_turn(
    state: AgentState,
    payload: dict[str, Any],
    *,
    created: bool,
) -> AgentState:
    """
    Mission 轮次准备：图 invoke 前处理 mission 状态。

    首轮 created=True：init_mission_state(payload)，绑定 domain pack。
    续轮有 goal：apply_steer_message；有 execution_grant 则机械 apply_mission_step_to_payload，
    否则清除 skip_planning_llm 等待 planning 解释 steer。
    编排未完成且 COMPLETED 无 grant 时保持 MISSION_PAUSED。

    Prepare mission state before graph invoke.
    First turn: init_mission_state; continuation with goal uses steer and optional grant.
    """
    from app.services.mission_orchestrator import orchestration_enabled, work_plan_completed
    from app.services.mission_steer import apply_steer_message

    if not should_use_mission_runtime(payload, str(state.get("execution_mode") or "")):
        return state

    goal = str(payload.get("goal") or "").strip()
    if not created and state.get("mission"):
        if goal:
            state = apply_steer_message(state, goal)
            payload_after = dict(state.get("input_payload") or {})
            from app.services.mission_execution import has_execution_grant
            from app.services.mission_schema import apply_mission_step_to_payload

            if has_execution_grant(payload_after):
                payload_after = apply_mission_step_to_payload(state)
                state = merge_state(
                    state,
                    input_payload=payload_after,
                    status=TaskStatus.MISSION_RUNNING.value,
                )
            else:
                payload_after["skip_planning_llm"] = False
                payload_after["writing_intent"] = {
                    "enabled": False,
                    "source": "await_steer_planning",
                }
                payload_after.pop("current_work_item", None)
                state = merge_state(
                    state,
                    input_payload=payload_after,
                    status=TaskStatus.MISSION_RUNNING.value,
                )
        mission = state.get("mission") or {}
        from app.services.mission_execution import has_execution_grant

        if orchestration_enabled(mission) and not work_plan_completed(state):
            prev = str(state.get("status") or "")
            payload_check = state.get("input_payload") or {}
            if prev in (
                TaskStatus.COMPLETED.value,
                TaskStatus.MISSION_PAUSED.value,
            ) and not has_execution_grant(payload_check):
                state = merge_state(state, status=TaskStatus.MISSION_PAUSED.value)
            elif has_execution_grant(payload_check):
                state = merge_state(state, status=TaskStatus.MISSION_RUNNING.value)
        return merge_state(state, execution_mode="mission")

    return init_mission_state(state, payload)


def _begin_task_graph_run(state: AgentState) -> tuple[AgentState, str]:
    """Register in-process executor; persist execution_run on state."""
    task_id = str(state["task_id"])
    run_id = begin_graph_run(task_id)
    return merge_state(state, execution_run=execution_run_meta(run_id)), run_id


def _end_task_graph_run(task_id: str, run_id: str) -> None:
    """Drop executor registry and live snapshot when the graph thread finishes."""
    end_graph_run(task_id, run_id)
    clear_live(task_id)


def _format_stream_exception(exc: BaseException) -> str:
    """Human-readable stream error; many library exceptions have an empty str()."""
    text = str(exc).strip()
    if text:
        return text
    name = type(exc).__name__
    if name == "GeneratorExit":
        return "stream closed (client disconnected or concurrent request interrupted execution)"
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


def _drain_trace_queue(task_id: str, trace_q: queue.SimpleQueue[dict[str, Any]]) -> Iterator[str]:
    while True:
        try:
            trace = trace_q.get_nowait()
        except queue.Empty:
            break
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


def _drain_answer_queue(task_id: str, answer_q: queue.SimpleQueue[dict[str, Any]]) -> Iterator[str]:
    while True:
        try:
            item = answer_q.get_nowait()
        except queue.Empty:
            break
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


def _drain_thinking_queue(
    task_id: str, thinking_q: queue.SimpleQueue[dict[str, Any]]
) -> Iterator[str]:
    while True:
        try:
            item = thinking_q.get_nowait()
        except queue.Empty:
            break
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
    task_id: str, writing_q: queue.SimpleQueue[dict[str, Any]]
) -> Iterator[str]:
    while True:
        try:
            item = writing_q.get_nowait()
        except queue.Empty:
            break
        yield _format_writing_delta_event(task_id, item)


def _drain_sse_side_queues(
    task_id: str,
    trace_q: queue.SimpleQueue[dict[str, Any]],
    answer_q: queue.SimpleQueue[dict[str, Any]],
    thinking_q: queue.SimpleQueue[dict[str, Any]] | None = None,
    writing_q: queue.SimpleQueue[dict[str, Any]] | None = None,
) -> Iterator[str]:
    if thinking_q is not None:
        yield from _drain_thinking_queue(task_id, thinking_q)
    if writing_q is not None:
        yield from _drain_writing_queue(task_id, writing_q)
    yield from _drain_answer_queue(task_id, answer_q)
    yield from _drain_trace_queue(task_id, trace_q)


def _maybe_pause_for_review(state: AgentState) -> AgentState:
    if (
        state.get("policy_result") == "REVIEW"
        and not state.get("review_feedback")
        and state.get("status") != TaskStatus.WAITING_REVIEW.value
    ):
        return human_review_node(state)
    return state


def _emit_tool_preview(state: AgentState) -> Iterator[str]:
    for item in state.get("tool_results") or []:
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        preview: dict[str, Any] = {"tool": item.get("tool"), "status": item.get("status")}
        if result.get("result") is not None:
            preview["snippet"] = str(result.get("result"))[:200]
        elif result.get("model_name"):
            preview["snippet"] = str(result.get("model_name"))
        elif result.get("path"):
            preview["snippet"] = str(result.get("path"))
        elif result.get("summary"):
            preview["snippet"] = str(result.get("summary"))[:200]
        yield _format_stream_event(
            "tool_preview",
            {"task_id": state["task_id"], **preview},
        )


_MISSION_QUIET_NODES = frozenset(
    {
        "mission_init",
        "mission_decide",
        "mission_act",
        "mission_observe",
        "mission_eval",
    }
)


def _should_emit_node_event(node_name: str, state: AgentState) -> bool:
    """Emit all node events so UI flow graph works for all execution modes."""
    return True


def _node_stream_payload(state: AgentState, node_name: str) -> dict[str, Any]:
    manuscript = state.get("manuscript") or {}
    writing_intent = (state.get("input_payload") or {}).get("writing_intent") or {}
    errors = list(state.get("errors") or [])
    status = str(state.get("status") or "")
    payload: dict[str, Any] = {
        "task_id": state["task_id"],
        "node": node_name,
        "status": status,
        "current_node": state.get("current_node"),
        "policy_result": state.get("policy_result"),
        "mission_step": state.get("mission_step"),
        "writing_action": writing_intent.get("action"),
        "body_bytes": manuscript.get("body_bytes"),
        "body_path": manuscript.get("body_path"),
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

    对外 API：start_task、stream_task、steer_mission、prepare_resume_mission、resume_task。

    Facade over compiled LangGraph graphs: start_task, stream_task, steer_mission,
    prepare_resume_mission, resume_task (human review resume via resume_graph).
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
        """
        按 execution_mode 选择 run_*_graph；handoff 时先 stream 主图 planning 再切 mission_graph。

        Select graph by execution_mode; optional stream main graph until planning then mission_graph.
        """
        try:
            if mode == "supervisor":
                return run_supervisor_graph(state)
            if mode == "exploration":
                return run_exploration_graph(state, thread_id=thread)
            if mode == "mission":
                return run_mission_graph(state, thread_id=thread)
            payload = state.get("input_payload") or {}
            if payload.get("enable_planning_mission_handoff"):
                latest: AgentState = state
                for node_name, snapshot in stream_graph(state, thread_id=thread):
                    latest = snapshot
                    snap_payload = latest.get("input_payload") or {}
                    exec_mode = str(latest.get("execution_mode") or mode).lower()
                    if node_name == "planning" and should_use_mission_runtime(
                        snap_payload, exec_mode
                    ):
                        mission_state = init_mission_state(latest, snap_payload)
                        return run_mission_graph(mission_state, thread_id=thread)
                return latest
            return run_graph(state, thread_id=thread)
        except Exception as exc:
            handled = handle_invoke_failure(thread, exc, state=state)
            if handled and handled.get("status") == "checkpoint_reset":
                if mode == "exploration":
                    return run_exploration_graph(state, thread_id=thread)
                if mode == "mission":
                    return run_mission_graph(state, thread_id=thread)
                return run_graph(state, thread_id=thread)
            raise

    def _finalize_turn(self, state: AgentState) -> AgentState:
        from app.services.otel_export import finalize_trace_export

        state = finalize_trace_export(state)
        return finalize_turn_history(state)

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
        if should_use_mission_runtime(payload, mode):
            goal = str(payload.get("goal") or "").strip()
            from app.services.session_goal import should_enter_mission_runtime

            if should_enter_mission_runtime(state, payload, goal):
                mode = "mission"
                state = _prepare_mission_for_turn(state, payload, created=created)
            else:
                mode = "single"
                state = merge_state(state, execution_mode="single", mission=None)
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
        if should_use_mission_runtime(payload, mode):
            goal = str(payload.get("goal") or "").strip()
            from app.services.session_goal import should_enter_mission_runtime

            if should_enter_mission_runtime(state, payload, goal):
                state = _prepare_mission_for_turn(state, payload, created=created)
                state = merge_state(state, execution_mode="mission")
            else:
                state = merge_state(state, execution_mode="single", mission=None)
        elif mode == "exploration":
            state = merge_state(state, execution_mode="exploration")
            state = merge_state(state, execution_mode="exploration")
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
        单任务 SSE：主线程轮询 node_q 与侧信道队列，工作线程跑 stream_*_graph。

        侧信道：progress_q、trace_q、answer_q、thinking_q、writing_q。
        planning 后可 handoff 到 stream_mission_graph。

        SSE pump: caller thread drains queues; worker runs stream_*_graph; optional mission handoff.
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
        state, run_id = _begin_task_graph_run(state)
        get_state_store().save(state)
        register_live(state)
        progress_q: queue.SimpleQueue[str] = queue.SimpleQueue()
        trace_q: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        answer_q: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        thinking_q: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        writing_q: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        node_q: queue.SimpleQueue[tuple[str, AgentState] | None] = queue.SimpleQueue()
        stream_error: list[BaseException | None] = [None]
        last_event_at = started_at

        def _capture_progress(message: str) -> None:
            progress_q.put(message)

        def _capture_trace(trace: dict[str, Any]) -> None:
            trace_q.put(trace)

        def _capture_answer(delta: dict[str, Any]) -> None:
            answer_q.put(delta)

        def _capture_thinking(delta: dict[str, Any]) -> None:
            thinking_q.put(delta)

        def _capture_writing(delta: dict[str, Any]) -> None:
            writing_q.put(delta)

        exec_mode = str(state.get("execution_mode", "")).lower()
        use_mission = exec_mode == "mission"
        use_exploration = exec_mode == "exploration"

        def _run_graph() -> None:
            try:
                if use_exploration:
                    stream_fn = stream_exploration_graph
                    graphs = [(stream_fn, state)]
                elif use_mission:
                    stream_fn = stream_mission_graph
                    graphs = [(stream_fn, state)]
                else:
                    graphs = [(stream_graph, state)]

                for stream_fn, run_state in graphs:
                    handoff_mission = False
                    for node_name, snapshot in stream_fn(
                        run_state, thread_id=graph_thread_id(run_state)
                    ):
                        node_q.put((node_name, snapshot))
                        if (
                            stream_fn is stream_graph
                            and node_name == "planning"
                            and should_use_mission_runtime(
                                snapshot.get("input_payload") or {},
                                str(snapshot.get("execution_mode") or ""),
                            )
                        ):
                            mission_state = init_mission_state(
                                snapshot, snapshot.get("input_payload") or {}
                            )
                            graphs.append((stream_mission_graph, mission_state))
                            handoff_mission = True
                            break
                    if handoff_mission:
                        continue
            except GeneratorExit:
                raise
            except BaseException as exc:
                stream_error[0] = exc
            finally:
                node_q.put(None)
                _end_task_graph_run(task_id, run_id)

        yield _format_progress_event(
            task_id, "任务已开始，Agent 正在处理…", started_at=started_at, phase="started"
        )

        set_progress_handler(_capture_progress)
        set_trace_handler(_capture_trace)
        set_answer_handler(_capture_answer if answer_stream_enabled() else None)
        set_thinking_handler(_capture_thinking if thinking_stream_enabled() else None)
        set_writing_handler(_capture_writing if writing_stream_enabled() else None)
        worker = threading.Thread(target=_run_graph, daemon=True)
        worker.start()
        try:
            while worker.is_alive() or not node_q.empty():
                yield from _drain_sse_side_queues(
                    task_id, trace_q, answer_q, thinking_q, writing_q
                )
                while True:
                    try:
                        msg = progress_q.get_nowait()
                    except queue.Empty:
                        break
                    yield _format_progress_event(
                        task_id, msg, started_at=started_at, phase="working"
                    )
                    last_event_at = time.monotonic()

                now = time.monotonic()
                if now - last_event_at >= 8.0 and worker.is_alive():
                    yield _format_progress_event(
                        task_id,
                        "仍在处理中…",
                        started_at=started_at,
                        phase="heartbeat",
                    )
                    last_event_at = now

                try:
                    item = node_q.get(timeout=0.08)
                except queue.Empty:
                    yield from _drain_sse_side_queues(
                        task_id, trace_q, answer_q, thinking_q, writing_q
                    )
                    continue
                if item is None:
                    break

                yield from _drain_sse_side_queues(
                    task_id, trace_q, answer_q, thinking_q, writing_q
                )
                while True:
                    try:
                        msg = progress_q.get_nowait()
                    except queue.Empty:
                        break
                    yield _format_progress_event(
                        task_id, msg, started_at=started_at, phase="working"
                    )
                    last_event_at = time.monotonic()

                node_name, snapshot = item
                latest = snapshot
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
                    yield _format_stream_event(
                        "node",
                        _node_stream_payload(latest, node_name),
                    )
                if node_name == "planning" and latest.get("plan"):
                    yield _format_stream_event(
                        "plan",
                        {
                            "task_id": latest["task_id"],
                            "plan": latest.get("plan", []),
                            "selected_tools": latest.get("selected_tools", []),
                            "mission": (latest.get("input_payload") or {}).get("mission"),
                            "writing_intent": (latest.get("input_payload") or {}).get(
                                "writing_intent"
                            ),
                        },
                    )
                if node_name == "planning" and should_use_mission_runtime(
                    latest.get("input_payload") or {},
                    str(latest.get("execution_mode") or ""),
                ):
                    yield _format_progress_event(
                        task_id,
                        "检测到 mission 合同，转入长程执行循环…",
                        started_at=started_at,
                        phase="mission_handoff",
                    )
                last_event_at = time.monotonic()
                if node_name == "tool_execution":
                    yield from _emit_tool_preview(latest)
                if (
                    node_name == "reasoning"
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
                        yield _format_stream_event(
                            "answer_preview",
                            {"task_id": latest["task_id"], "text": summary},
                        )
                if latest.get("policy_result") == "REVIEW" and node_name == "policy":
                    interrupted_for_review = True

            yield from _drain_sse_side_queues(
                task_id, trace_q, answer_q, thinking_q, writing_q
            )
            while True:
                try:
                    msg = progress_q.get_nowait()
                except queue.Empty:
                    break
                yield _format_progress_event(
                    task_id, msg, started_at=started_at, phase="working"
                )

            if stream_error[0] is not None:
                yield from self._stream_error(latest, stream_error[0])
                return
        finally:
            set_progress_handler(None)
            set_trace_handler(None)
            set_answer_handler(None)
            set_thinking_handler(None)
            set_writing_handler(None)
            if worker is not threading.current_thread():
                worker.join(timeout=2.0)
            _end_task_graph_run(task_id, run_id)

        yield from self._stream_finalize(latest, interrupted_for_review)

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
        register_live(state)
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

    def _stream_finalize(self, latest: AgentState, interrupted_for_review: bool) -> Iterator[str]:
        if interrupted_for_review and latest.get("status") != TaskStatus.WAITING_REVIEW.value:
            latest = persist_turn_draft_answer(latest)
            latest = human_review_node(latest)
            get_state_store().save(latest)
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
        if latest.get("status") == TaskStatus.MISSION_PAUSED.value:
            from app.services.client_display import build_mission_paused_payload
            from app.services.mission_orchestrator import mission_is_autonomous
            from app.services.steer_confirmation_actions import confirmation_sse_fields

            payload_lp = latest.get("input_payload") or {}
            control = latest.get("mission_control") or {}
            steer_pause = "steer" in str(control.get("reason") or "").lower()
            autonomous = mission_is_autonomous(latest.get("mission") or {})
            paused_body = build_mission_paused_payload(
                latest,
                autonomous=autonomous,
                steer_pause=steer_pause,
                confirmation_actions=confirmation_sse_fields(latest["task_id"], payload_lp).get(
                    "confirmation_actions"
                ),
            )
            yield _format_stream_event("mission_paused", paused_body)
        done_payload_lp = latest.get("input_payload") or {}
        from app.services.mission_steer import review_outline_requested
        from app.services.confirmation.stream_display import (
            build_gate_sse_fields,
            client_final_answer,
        )

        yield _format_stream_event(
            "done",
            {
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
                "steer_review_only": review_outline_requested(done_payload_lp),
                **build_gate_sse_fields(latest["task_id"], latest),
            },
        )

    def steer_mission(
        self,
        task_id: str,
        message: str = "",
        *,
        intervention: Optional[dict[str, Any]] = None,
        confirm: bool = False,
        priority: int = 0,
        preempt: bool = False,
        replace_goal: bool = False,
    ) -> AgentState:
        """
        委托 mission_steer.queue_steer_message，由 API 层写 audit。

        Delegates to mission_steer.queue_steer_message.
        """
        from app.services.mission_steer import queue_steer_message

        updated = queue_steer_message(
            task_id,
            message,
            intervention=intervention,
            confirm=confirm,
            priority=priority,
            preempt=preempt,
            replace_goal=replace_goal,
        )
        get_audit_store().append_events(task_id, updated.get("audit_log", []))
        return updated

    def prepare_resume_mission(self, task_id: str, *, confirm: bool = False) -> AgentState:
        """
        恢复 Mission：处理 steer 确认门、发放 execution_grant，不直接 invoke 图。

        保留非 force-pause 的 pending_user_message；Web /confirm、task_api /resume 调用。

        Prepare resume: confirm steer gates, issue_execution_grant, MISSION_RUNNING, no graph invoke.
        """
        from app.services.mission_worker_lost import reconcile_worker_lost

        stored = get_state_store().load(task_id)
        if not stored:
            raise KeyError(f"Task not found: {task_id}")
        stored = reconcile_worker_lost(stored)
        status = str(stored.get("status", ""))
        if status not in (
            TaskStatus.MISSION_PAUSED.value,
            TaskStatus.REASONED.value,
        ):
            raise ValueError(f"Task {task_id} cannot resume from status {status}")

        from app.services.mission_steer_confirm import (
            confirm_steer_intent,
            steer_confirmation_pending,
        )
        from app.services.mission_steer_outcome_confirm import (
            confirm_steer_outcome,
            steer_outcome_confirmation_pending,
        )

        payload = stored.get("input_payload") or {}
        if steer_confirmation_pending(payload):
            if not confirm:
                raise ValueError(
                    f"Task {task_id} awaits steer intent confirmation; "
                    "POST /resume with {\"confirm\": true} or POST /steer with {\"confirm\": true}"
                )
            stored = confirm_steer_intent(stored)
            get_state_store().save(stored)
            payload = stored.get("input_payload") or {}
        if steer_outcome_confirmation_pending(payload):
            if not confirm:
                raise ValueError(
                    f"Task {task_id} awaits steer outcome confirmation; "
                    "POST /resume with {\"confirm\": true} or POST /steer with {\"confirm\": true}"
                )
            stored = confirm_steer_outcome(stored)
            get_state_store().save(stored)

        from app.services.mission_execution import issue_execution_grant
        from app.services.mission_steer import normalize_pending_entries

        pending_entries = normalize_pending_entries(stored.get("pending_user_message"))
        kept_entries: list[dict[str, Any]] = []
        for entry in pending_entries:
            intervention = entry.get("intervention")
            is_forced_pause = bool(
                isinstance(intervention, dict)
                and str(intervention.get("action") or "") == "pause"
                and bool(intervention.get("force"))
            )
            if not is_forced_pause:
                kept_entries.append(entry)
        pending_user_message = None
        if kept_entries:
            pending_user_message = {
                "queued_at": kept_entries[0].get("queued_at"),
                "messages": kept_entries,
                "message": "\n\n".join(
                    str(e.get("message") or "").strip()
                    for e in kept_entries
                    if e.get("message")
                ).strip(),
            }

        resumed = issue_execution_grant(
            merge_state(
                stored,
                status=TaskStatus.MISSION_RUNNING.value,
                mission_control=None,
                pending_user_message=pending_user_message,
            ),
            source="resume_api",
        )
        get_state_store().save(resumed)
        return resumed

    def resume_mission(self, task_id: str, *, confirm: bool = False) -> AgentState:
        """Continue an orchestrated mission from MISSION_PAUSED (one or more steps)."""
        resumed = self.prepare_resume_mission(task_id, confirm=confirm)
        resumed, run_id = _begin_task_graph_run(resumed)
        get_state_store().save(resumed)
        thread = graph_thread_id(resumed)
        try:
            final_state = self._run_with_slot(
                lambda: self._invoke_graph_safe(resumed, thread=thread, mode="mission")
            )
        finally:
            _end_task_graph_run(str(resumed["task_id"]), run_id)
        final_state = self._finalize_turn(final_state)
        get_state_store().save(final_state)
        get_audit_store().append_events(task_id, final_state.get("audit_log", []))
        return final_state

    def stream_resume_mission(self, task_id: str, *, confirm: bool = False) -> Iterator[str]:
        """SSE stream for mission resume (same events as /tasks/stream)."""
        resumed = self.prepare_resume_mission(task_id, confirm=confirm)
        yield from self._stream_single(resumed, created=False)

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
