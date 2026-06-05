"""
Observation layer — domain-agnostic facts after each mission step (generalizes turn_facts).

This records post-execution facts only. It does NOT replace pre-planning intent observation
(see app/services/intent_observation.py).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.domain.mission import Mission, Progress
from app.runtime.state import AgentState, TaskStatus
from app.services.conversation_context import conversation_history_from_state
from app.services.fact_layer import _tool_outcome_line


def build_observation(
    state: AgentState,
    *,
    mission: Optional[dict[str, Any]] = None,
    progress: Optional[dict[str, Any]] = None,
    mission_step: Optional[int] = None,
) -> dict[str, Any]:
    """Compile read-only snapshot: tools, writing, manuscript, mission context."""
    payload = state.get("input_payload") or {}
    tool_lines = [_tool_outcome_line(item) for item in (state.get("tool_results") or [])]
    writing_intent = payload.get("writing_intent") or {}
    manuscript = state.get("manuscript") or {}

    executed_actions: list[str] = []
    for line in tool_lines:
        if line.get("status") in ("ok", "success") or line.get("output") or line.get("path"):
            executed_actions.append(f"tool:{line['tool']}")
    write_statuses = ("WRITTEN", "TOOL_EXECUTED", "REASONED")
    phase_action = str(
        writing_intent.get("action")
        or (payload.get("writing_phase") if isinstance(payload.get("writing_phase"), str) else "")
        or ""
    )
    if state.get("status") in write_statuses and (
        writing_intent.get("enabled") or phase_action
    ):
        executed_actions.append(f"writing:{phase_action or writing_intent.get('action', 'write')}")
    if manuscript.get("body_path") and manuscript.get("body_bytes"):
        executed_actions.append(
            f"artifact:{manuscript['body_path']}:{manuscript['body_bytes']}B"
        )

    step = mission_step if mission_step is not None else int(state.get("mission_step") or 0)
    prog = progress or state.get("progress") or {}
    metrics = dict(prog.get("metrics") or {})

    mission_kind = str((mission or state.get("mission") or {}).get("kind", ""))
    if mission_kind == "writing" and manuscript:
        from app.domain.packs.registry import get_domain_pack

        try:
            pack = get_domain_pack("writing")
            fresh = pack.collect_metrics(state, {"manuscript": manuscript})
            metrics = {**metrics, **fresh}
        except KeyError:
            pass

    return {
        "schema": "observation/v1",
        "mission_step": step,
        "mission_id": (mission or state.get("mission") or {}).get("id"),
        "mission_kind": (mission or state.get("mission") or {}).get("kind"),
        "turn": int(state.get("session_turn") or 1),
        "task_id": state["task_id"],
        "session_id": state.get("session_id") or state["task_id"],
        "goal": str(payload.get("goal") or ""),
        "plan": list(state.get("plan") or []),
        "status": state.get("status"),
        "tools_executed": tool_lines,
        "writing_intent": writing_intent if writing_intent.get("enabled") else None,
        "manuscript": manuscript or None,
        "executed_actions": executed_actions,
        "tool_count": len(tool_lines),
        "has_failures": (
            str(state.get("status", ""))
            in (
                TaskStatus.WRITING_FAILED.value,
                TaskStatus.FAILED.value,
                TaskStatus.TOOL_FAILED.value,
                TaskStatus.REASON_FAILED.value,
            )
            or any(
                line.get("status") in ("error", "skipped") or line.get("error")
                for line in tool_lines
            )
        ),
        "progress_metrics": metrics,
        "reasoning_summary": (state.get("reasoning_result") or {}).get("summary"),
        "built_at": datetime.now(timezone.utc).isoformat(),
    }


def attach_observation(state: AgentState) -> AgentState:
    from app.runtime.state import merge_state

    obs = build_observation(state)
    payload = dict(state.get("input_payload") or {})
    payload["observation"] = obs
    payload["turn_facts"] = obs  # backward compat for reasoning_node
    history = list(state.get("observations") or [])
    history.append(obs)
    return merge_state(
        state,
        observation=obs,
        turn_facts=obs,
        observations=history,
        input_payload=payload,
    )


def reasoning_context_from_observation(state: AgentState) -> dict[str, Any]:
    """Build reasoning context using observation as ground truth."""
    from app.services.prompt_context_gateway import (
        context_governance_enabled,
        governed_mission_observation_context,
    )

    if context_governance_enabled():
        return governed_mission_observation_context(state)

    payload = state.get("input_payload") or {}
    obs = state.get("observation") or build_observation(state)
    memory_hits = state.get("memory_hits") or []
    mission = state.get("mission") or {}
    progress = state.get("progress") or {}

    return {
        "goal": payload.get("goal"),
        "session_turn": state.get("session_turn"),
        "mission": mission,
        "progress": progress,
        "conversation_history": conversation_history_from_state(state),
        "observation": obs,
        "turn_facts": obs,
        "retrieved_knowledge": state.get("retrieved_knowledge") or [],
        "memory_hits": memory_hits[:5],
        "instructions": (
            "Answer ONLY based on observation / turn_facts for what already executed. "
            "Do NOT claim actions absent from observation.executed_actions. "
            "For long missions, reference progress.metrics vs mission.success_criteria."
        ),
    }
