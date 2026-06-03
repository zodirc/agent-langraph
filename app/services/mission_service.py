"""Mission 生命周期
init_mission_state: resolve_mission_pack → mission dict + progress → graph_runner

Mission lifecycle — init, progress, domain pack resolution.
mission_graph entry.
should_run_mission_runtime: gate before switching execution_mode to mission."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.domain.packs.registry import resolve_mission_pack
from app.runtime.state import AgentState, TaskStatus, merge_state
from app.services.mission_schema import (
    apply_mission_step_to_payload,
    build_mission_dict,
    mission_block_from_payload,
    should_use_mission_runtime,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def should_run_mission_runtime(state: AgentState, payload: dict[str, Any]) -> bool:
    return should_use_mission_runtime(
        payload, str(state.get("execution_mode") or "")
    )


def init_mission_state(
    state: AgentState,
    payload: Optional[dict[str, Any]] = None,
    *,
    mission_kind: Optional[str] = None,
) -> AgentState:
    """Attach mission + initial progress from domain pack."""
    from app.services.mission_orchestrator import ensure_work_plan, orchestration_enabled

    payload = dict(payload or state.get("input_payload") or {})
    pack = resolve_mission_pack(
        mission_kind=mission_kind,
        task_type=state.get("task_type"),
        payload=payload,
    )
    block = mission_block_from_payload(payload) or {}
    if block and not block.get("kind"):
        block = {**block, "kind": pack.name}
    mission = pack.parse_mission(state, payload, mission_block=block)

    auto = str(mission.get("execution_mode", "")).lower() == "autonomous" or bool(
        (mission.get("constraints") or {}).get("no_human")
    )
    if orchestration_enabled(mission) and not mission.get("orchestration"):
        mission = {
            **mission,
            "orchestration": {
                "enabled": True,
                "stepwise": not auto,
                "auto_decompose": True,
            },
        }
    if orchestration_enabled(mission):
        constraints = dict(mission.get("constraints") or {})
        orch = dict(mission.get("orchestration") or {})
        orch.setdefault("enabled", True)
        if auto:
            constraints["no_human"] = True
            orch.setdefault("stepwise", False)
            mission = {
                **mission,
                "constraints": constraints,
                "execution_mode": "autonomous",
                "orchestration": orch,
            }
        else:
            constraints.setdefault("no_human", False)
            orch.setdefault("stepwise", True)
            mission = {
                **mission,
                "constraints": constraints,
                "execution_mode": "interactive",
                "orchestration": orch,
            }

    progress = {
        "phase": "executing",
        "steps_completed": 0,
        "metrics": {},
        "blockers": [],
        "consecutive_failures": 0,
        "started_at": _now_iso(),
        "updated_at": _now_iso(),
    }

    updated = merge_state(
        state,
        mission=mission,
        progress=progress,
        mission_step=0,
        observations=[],
        observation=None,
        step_decision=None,
        execution_mode="mission",
        status=TaskStatus.NEW.value,
        input_payload={**payload, "mission": mission},
    )
    return ensure_work_plan(updated)


def update_progress_from_observation(state: AgentState) -> AgentState:
    """Merge pack metrics into progress after a step."""
    mission = state.get("mission") or {}
    observation = state.get("observation") or {}
    kind = str(mission.get("kind", "single_turn"))

    from app.domain.packs.registry import get_domain_pack

    try:
        pack = get_domain_pack(kind)
        metrics = pack.collect_metrics(state, observation)
    except KeyError:
        metrics = {"steps": int(state.get("mission_step") or 0)}

    progress = dict(state.get("progress") or {})
    progress["metrics"] = {**dict(progress.get("metrics") or {}), **metrics}
    progress["updated_at"] = _now_iso()
    progress["steps_completed"] = int(state.get("mission_step") or 0)

    if observation.get("has_failures"):
        progress["consecutive_failures"] = int(progress.get("consecutive_failures", 0)) + 1
    else:
        progress["consecutive_failures"] = 0

    return merge_state(state, progress=progress)


def prepare_state_for_mission_act(state: AgentState) -> AgentState:
    """Apply steer, work-plan item, or step_policy writing_intent before act."""
    from app.services.mission_orchestrator import (
        apply_work_plan_to_payload,
        orchestration_enabled,
    )
    from app.services.mission_schema import apply_mission_step_to_payload
    from app.services.mission_steer import (
        consume_pending_steer,
        review_outline_requested,
        steer_requires_planning,
    )

    state = consume_pending_steer(state)

    if orchestration_enabled(state.get("mission") or {}):
        state = apply_work_plan_to_payload(state)
        from app.services.writing_phases import apply_writing_phase_from_decision

        state = apply_writing_phase_from_decision(state)
        payload = dict(state.get("input_payload") or {})
        if review_outline_requested(payload):
            from app.services.mission_steer import apply_review_outline_mode

            payload = apply_review_outline_mode(payload, state.get("mission") or {})
            return merge_state(state, input_payload=payload)
        if steer_requires_planning(payload):
            payload["skip_planning_llm"] = False
            payload["writing_intent"] = {
                "enabled": False,
                "source": "await_steer_planning",
            }
            return merge_state(state, input_payload=payload)
        item = payload.get("current_work_item") or {}
        if str(item.get("kind")) == "human_gate":
            return state
        if str(item.get("kind")) == "edit_plot":
            return state
        if not (payload.get("writing_intent") or {}).get("enabled"):
            payload = apply_mission_step_to_payload(state)
            return merge_state(state, input_payload=payload)
        return state

    payload = apply_mission_step_to_payload(state)
    if steer_requires_planning(payload):
        payload["skip_planning_llm"] = False
        payload["writing_intent"] = {
            "enabled": False,
            "source": "await_steer_planning",
        }
    return merge_state(state, input_payload=payload)


def bump_mission_step(state: AgentState) -> AgentState:
    step = int(state.get("mission_step") or 0) + 1
    return merge_state(state, mission_step=step)


def mark_mission_phase(state: AgentState, phase: str) -> AgentState:
    progress = dict(state.get("progress") or {})
    progress["phase"] = phase
    progress["updated_at"] = _now_iso()
    return merge_state(state, progress=progress)
