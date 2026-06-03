"""
Self-Routed Deliberation Loop (SRDL) runner — bounded action execution and loop control.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from app.config.settings import settings
from app.domain.react_loop import (
    ALL_REACT_ACTIONS,
    ReactDecision,
    ReactLoopState,
    ReactTraceItem,
    get_react_loop,
    merge_react_loop,
)
from app.runtime.state import AgentState, TaskStatus, merge_state
from app.services.fact_layer import attach_turn_facts
from app.services.react_audit import record_react_event
from app.services.turn_contract import contract_tool_names


def _goal_from_state(state: AgentState) -> str:
    payload = state.get("input_payload") or {}
    return str(payload.get("query") or payload.get("goal") or "")


def _effective_tools(state: AgentState) -> list[str]:
    tools = list(state.get("selected_tools") or [])
    if tools:
        return tools
    return contract_tool_names(state.get("input_payload") or {})


def ensure_loop_initialized(state: AgentState) -> AgentState:
    loop = get_react_loop(state)
    if loop.enabled and loop.status == "running":
        return state
    from app.services.react_entry import init_react_loop_state
    from app.services.metrics_service import get_metrics_service

    get_metrics_service().inc_react_loop_entered()
    return merge_state(state, react_loop=init_react_loop_state(state))


def rule_based_decision(state: AgentState, loop: ReactLoopState) -> ReactDecision:
    """Deterministic fallback when LLM is disabled or fails."""
    allowed = set(loop.allowed_actions or ALL_REACT_ACTIONS)
    knowledge = state.get("retrieved_knowledge") or []
    tools = _effective_tools(state)
    tool_results = state.get("tool_results") or []
    memories = state.get("memory_hits") or []
    reasoning = state.get("reasoning_result") or {}

    if "retrieve_knowledge" in allowed and not knowledge:
        return ReactDecision(
            thought_summary="缺少外部知识依据，先检索知识库",
            action="retrieve_knowledge",
            action_input={"query": _goal_from_state(state)},
            continue_loop=True,
            why="需要补充事实依据",
            confidence=0.7,
        )
    if "retrieve_memory" in allowed and not memories and not knowledge:
        return ReactDecision(
            thought_summary="检查会话记忆以承接上下文",
            action="retrieve_memory",
            action_input={"query": _goal_from_state(state)},
            continue_loop=True,
            why="多轮上下文可能相关",
            confidence=0.65,
        )
    if "call_tool" in allowed and tools and not tool_results:
        return ReactDecision(
            thought_summary="需要调用工具完成子任务",
            action="call_tool",
            action_input={"tools": tools},
            continue_loop=True,
            why="已选工具尚未执行",
            confidence=0.75,
        )
    if reasoning.get("answer") or reasoning.get("summary"):
        return ReactDecision(
            thought_summary="已有中间推理结果，结束审议回路",
            action="finish",
            action_input={},
            continue_loop=False,
            why="信息已充分",
            confidence=0.8,
        )
    if "reason" in allowed:
        return ReactDecision(
            thought_summary="基于当前事实做中间总结",
            action="reason",
            action_input={},
            continue_loop=True,
            why="整合已有观察",
            confidence=0.7,
        )
    return ReactDecision(
        thought_summary="无更多动作，结束回路",
        action="finish",
        action_input={},
        continue_loop=False,
        why="默认结束",
        confidence=0.5,
    )


def llm_decision(state: AgentState, loop: ReactLoopState) -> ReactDecision | None:
    """LLM deliberation when REACT_LOOP_LLM_DECIDE is enabled."""
    if not getattr(settings, "REACT_LOOP_LLM_DECIDE", False):
        return None
    if not settings.MODEL_ENABLED:
        return None

    from app.config.prompts import REACT_DELIBERATE_SYSTEM
    from app.services.fact_layer import build_turn_facts
    from app.services.llm_client import invoke_structured

    payload = {
        "goal": loop.goal or _goal_from_state(state),
        "allowed_actions": loop.allowed_actions,
        "step_index": loop.step_index,
        "max_steps": loop.max_steps,
        "history": [h.to_dict() for h in loop.history],
        "turn_facts": build_turn_facts(state),
        "selected_tools": _effective_tools(state),
    }
    try:
        raw = invoke_structured(
            "routing",
            REACT_DELIBERATE_SYSTEM,
            json.dumps(payload, ensure_ascii=False),
        )
        decision = ReactDecision.from_dict(raw)
        if decision.action not in loop.allowed_actions:
            return None
        return decision
    except (ValueError, RuntimeError, KeyError, TypeError):
        return None


def deliberate_next_action(state: AgentState) -> tuple[AgentState, ReactDecision]:
    state = ensure_loop_initialized(state)
    loop = get_react_loop(state)
    decision = llm_decision(state, loop) or rule_based_decision(state, loop)

    loop.current_decision = decision.to_dict()
    if decision.route_recommendation:
        loop.route_recommendation = decision.route_recommendation.to_dict()

    state = merge_react_loop(state, loop)
    state = record_react_event(
        state,
        "react_step_started",
        f"step_{loop.step_index + 1}",
        "react_deliberate",
        {"step_index": loop.step_index, "max_steps": loop.max_steps},
    )
    state = record_react_event(
        state,
        "react_action_selected",
        decision.action,
        "react_deliberate",
        {
            "action": decision.action,
            "thought_summary": decision.thought_summary,
            "confidence": decision.confidence,
            "why": decision.why,
        },
    )
    return state, decision


def _execute_retrieve_knowledge(state: AgentState, decision: ReactDecision) -> tuple[AgentState, dict]:
    from app.services.knowledge_store import get_knowledge_store
    from app.services.memory_query import build_memory_search_query
    from app.services.retrieval_content_sanitizer import sanitize_retrieved_batch
    from app.services.retrieval_policy import skip_knowledge_retrieval

    query = str(decision.action_input.get("query") or build_memory_search_query(state))
    if skip_knowledge_retrieval(state):
        knowledge: list[dict] = []
    else:
        knowledge = get_knowledge_store().hybrid_search(query)
    knowledge = sanitize_retrieved_batch(knowledge)
    state = merge_state(
        state,
        retrieved_knowledge=knowledge,
        status=TaskStatus.RETRIEVED.value,
    )
    return state, {
        "action": "retrieve_knowledge",
        "knowledge_count": len(knowledge),
        "query": query[:200],
        "status": "ok",
    }


def _execute_retrieve_memory(state: AgentState, decision: ReactDecision) -> tuple[AgentState, dict]:
    from app.services.memory_query import build_memory_search_query
    from app.services.memory_store import get_memory_store
    from app.services.retrieval_content_sanitizer import sanitize_retrieved_batch
    from app.services.code_artifact_pipeline import filter_memory_hits

    from app.services.retrieval_policy import (
        restrict_memory_to_current_session,
        should_skip_session_memory_retrieval,
    )

    query = str(decision.action_input.get("query") or build_memory_search_query(state))
    if should_skip_session_memory_retrieval(state):
        memories: list[dict] = []
    else:
        memories = get_memory_store().search_for_context(
            query,
            user_id=state.get("user_id", "anonymous"),
            task_id=state["task_id"],
            session_id=state.get("session_id") or state["task_id"],
            restrict_to_session=restrict_memory_to_current_session(state),
        )
    memories = filter_memory_hits(state, memories)
    memories = sanitize_retrieved_batch(memories)
    state = merge_state(state, memory_hits=memories, status=TaskStatus.RETRIEVED.value)
    return state, {
        "action": "retrieve_memory",
        "memory_count": len(memories),
        "query": query[:200],
        "status": "ok",
    }


def _execute_call_tool(state: AgentState, decision: ReactDecision) -> tuple[AgentState, dict]:
    from app.nodes.tool_node import tool_execution_node

    payload = state.get("input_payload") or {}
    if payload.get("worker_react") or (state.get("react_loop") or {}).get("mode") == "oma_worker":
        allowed = frozenset(
            {"read_text_artifact", "grep_file", "read_file", "ls_path", "grep_file"}
        )
        tools = [
            t
            for t in (decision.action_input.get("tools") or _effective_tools(state))
            if str(t) in allowed
        ]
        if not tools:
            return state, {
                "action": "call_tool",
                "status": "blocked",
                "error": "oma_react_forbids_artifact_write",
            }
    else:
        tools = decision.action_input.get("tools") or _effective_tools(state)
    if tools and not state.get("selected_tools"):
        state = merge_state(state, selected_tools=list(tools))
    updated = tool_execution_node(state)
    results = updated.get("tool_results") or []
    failed = any(str(r.get("status")) in ("failed", "error", "blocked") for r in results)
    return updated, {
        "action": "call_tool",
        "tool_count": len(results),
        "status": "failed" if failed else "ok",
        "tools": [r.get("tool") for r in results],
    }


def _execute_reason(state: AgentState, decision: ReactDecision) -> tuple[AgentState, dict]:
    from app.services.fact_layer import build_turn_facts
    from app.services.llm_client import invoke_structured
    from app.config.prompts import REACT_INTERMEDIATE_REASON_SYSTEM

    facts = build_turn_facts(state)
    payload = {
        "goal": _goal_from_state(state),
        "turn_facts": facts,
        "instruction": decision.action_input.get("instruction") or "Summarize findings for the user goal.",
    }
    summary = ""
    confidence = 0.6
    if settings.MODEL_ENABLED and getattr(settings, "REACT_LOOP_LLM_DECIDE", False):
        try:
            raw = invoke_structured(
                "routing",
                REACT_INTERMEDIATE_REASON_SYSTEM,
                json.dumps(payload, ensure_ascii=False),
            )
            summary = str(raw.get("summary") or raw.get("answer") or "")
            confidence = float(raw.get("confidence") or 0.6)
        except (ValueError, RuntimeError, KeyError):
            pass
    if not summary:
        parts = []
        k = len(state.get("retrieved_knowledge") or [])
        m = len(state.get("memory_hits") or [])
        t = len(state.get("tool_results") or [])
        if k:
            parts.append(f"{k} knowledge hits")
        if m:
            parts.append(f"{m} memory hits")
        if t:
            parts.append(f"{t} tool results")
        summary = f"Intermediate synthesis: {', '.join(parts) or 'no new facts'}."
    reasoning_result = {
        "answer": summary,
        "summary": summary,
        "confidence": confidence,
        "mode": "react_intermediate",
        "structured": {"steps": [summary], "conclusion": summary},
    }
    state = merge_state(
        state,
        reasoning_result=reasoning_result,
        status=TaskStatus.REASONED.value,
    )
    return state, {
        "action": "reason",
        "summary_preview": summary[:300],
        "confidence": confidence,
        "status": "ok",
    }


def _execute_replan(state: AgentState, decision: ReactDecision) -> tuple[AgentState, dict]:
    from app.services.task_agenda import local_replan_slice, ensure_agenda_fields

    loop = get_react_loop(state)
    loop.replan_count += 1
    progress = dict(state.get("progress") or {})
    work_plan = progress.get("work_plan")
    item_id = str(decision.action_input.get("item_id") or "")

    new_plan_steps: list[str] = []
    if isinstance(work_plan, dict) and item_id:
        work_plan = local_replan_slice(ensure_agenda_fields(work_plan), item_id)
        progress["work_plan"] = work_plan
    else:
        plan = list(state.get("plan") or [])
        rationale = str(decision.action_input.get("rationale") or decision.why or "path blocked")
        new_plan_steps = [
            f"Replan: {rationale[:80]}",
            "retrieve supporting facts",
            "reason with updated facts",
        ]
        plan = new_plan_steps + plan[:2]
        state = merge_state(state, plan=plan)

    state = merge_react_loop(state, loop)
    state = record_react_event(
        state,
        "react_replanned",
        item_id or "plan",
        "react_execute",
        {"replan_count": loop.replan_count, "new_steps": new_plan_steps[:3]},
    )
    return state, {
        "action": "replan",
        "replan_count": loop.replan_count,
        "item_id": item_id or None,
        "status": "ok",
    }


def execute_bounded_action(state: AgentState, decision: ReactDecision) -> tuple[AgentState, dict]:
    action = decision.action
    if action not in ALL_REACT_ACTIONS:
        return state, {"action": action, "status": "blocked", "error": "action_not_allowed"}

    if action == "finish":
        return state, {"action": "finish", "status": "ok"}

    executors = {
        "retrieve_knowledge": _execute_retrieve_knowledge,
        "retrieve_memory": _execute_retrieve_memory,
        "call_tool": _execute_call_tool,
        "reason": _execute_reason,
        "replan": _execute_replan,
    }
    executor = executors.get(action)
    if not executor:
        return state, {"action": action, "status": "blocked", "error": "no_executor"}

    try:
        updated, observation = executor(state, decision)
        updated = record_react_event(
            updated,
            "react_action_executed",
            action,
            "react_execute",
            observation,
        )
        return updated, observation
    except Exception as exc:
        return merge_state(
            state,
            errors=list(state.get("errors", [])) + [f"react_execute:{action}: {exc}"],
        ), {"action": action, "status": "failed", "error": str(exc)}


def summarize_observation(observation: dict[str, Any]) -> str:
    action = str(observation.get("action") or "")
    status = str(observation.get("status") or "ok")
    if action == "retrieve_knowledge":
        return f"检索知识库: {observation.get('knowledge_count', 0)} 条 ({status})"
    if action == "retrieve_memory":
        return f"检索记忆: {observation.get('memory_count', 0)} 条 ({status})"
    if action == "call_tool":
        tools = observation.get("tools") or []
        return f"工具执行: {', '.join(str(t) for t in tools) or 'none'} ({status})"
    if action == "reason":
        preview = str(observation.get("summary_preview") or "")[:120]
        return f"中间推理: {preview} ({status})"
    if action == "replan":
        return f"局部重规划 #{observation.get('replan_count', 0)} ({status})"
    if action == "finish":
        return "结束审议回路"
    if observation.get("error"):
        return f"{action} 失败: {observation['error']}"
    return f"{action}: {status}"


def record_react_observation(
    state: AgentState,
    decision: ReactDecision,
    observation: dict[str, Any],
) -> AgentState:
    loop = get_react_loop(state)
    obs_summary = summarize_observation(observation)
    trace = ReactTraceItem(
        step=loop.step_index + 1,
        thought_summary=decision.thought_summary,
        action=decision.action,
        action_input=dict(decision.action_input),
        observation_summary=obs_summary,
        confidence=decision.confidence,
        continue_loop=decision.continue_loop,
        why=decision.why,
    )
    loop.history.append(trace)
    loop.step_index += 1
    loop.current_decision = None
    state = merge_react_loop(state, loop)
    state = attach_turn_facts(state)
    state = record_react_event(
        state,
        "react_observation_recorded",
        decision.action,
        "react_observe",
        {"observation_summary": obs_summary, "observation": observation},
    )
    return state


def should_finish_loop(
    state: AgentState,
    loop: ReactLoopState,
    decision: ReactDecision,
    observation: dict[str, Any],
) -> bool:
    if decision.action == "finish":
        return True
    if not decision.continue_loop:
        return True
    if loop.step_index >= loop.max_steps:
        return True
    if str(observation.get("status")) == "ok" and decision.action == "reason":
        reasoning = state.get("reasoning_result") or {}
        if float(reasoning.get("confidence") or 0) >= 0.7:
            return True
    return False


def should_abort_loop(
    state: AgentState,
    loop: ReactLoopState,
    observation: dict[str, Any],
) -> tuple[bool, str]:
    max_replan = int(getattr(settings, "REACT_LOOP_MAX_REPLAN", 2))
    if loop.replan_count > max_replan:
        return True, "max_replan_exceeded"
    if str(state.get("status")) == TaskStatus.FAILED.value:
        return True, "state_failed"
    if str(observation.get("status")) == "blocked":
        return True, "action_blocked"
    failures = sum(
        1 for h in loop.history if "失败" in h.observation_summary or "failed" in h.observation_summary
    )
    if failures >= int(getattr(settings, "REACT_LOOP_MAX_FAILURES", 2)):
        return True, "consecutive_failures"
    return False, ""


def finalize_loop(
    state: AgentState,
    *,
    reason: str,
    exit_path: str = "finish_with_answer",
) -> AgentState:
    loop = get_react_loop(state)
    loop.status = "finished" if exit_path.startswith("finish") else "aborted"
    loop.exit_reason = reason
    loop.exit_path = exit_path
    loop.enabled = False
    state = merge_react_loop(state, loop)
    event = "react_loop_finished" if loop.status == "finished" else "react_loop_aborted"
    state = record_react_event(
        state,
        event,
        exit_path,
        "react_continue",
        {
            "exit_reason": reason,
            "exit_path": exit_path,
            "step_count": loop.step_index,
            "history_len": len(loop.history),
        },
    )
    from app.services.react_audit import export_react_loop_prometheus

    export_react_loop_prometheus(state)
    return state
