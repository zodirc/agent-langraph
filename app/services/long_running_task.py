"""
Long-running task runtime — domain-agnostic mission step resolution.

Writing-specific paths (turn_contract, forced intervention) stay in writing pack;
other domains use action_resolver + domain pack mapping.
"""

from __future__ import annotations

from typing import Any, Optional

from app.runtime.state import AgentState, merge_state


def resolve_mission_pack_for_state(state: AgentState, mission: dict[str, Any]):
    from app.domain.packs.registry import resolve_mission_pack

    payload = state.get("input_payload") or {}
    return resolve_mission_pack(
        mission_kind=str(mission.get("kind") or ""),
        task_type=str(state.get("task_type") or ""),
        payload=payload,
    )


def resolve_step_intent(
    state: AgentState,
    *,
    mission: dict[str, Any],
) -> dict[str, Any]:
    """Resolve next-step execution intent via domain pack."""
    kind = str(mission.get("kind") or "writing")
    if kind == "writing":
        from app.services.mission_schema import resolve_writing_intent_for_step

        return resolve_writing_intent_for_step(state, mission=mission)

    from app.services.action_resolver import select_action

    pack = resolve_mission_pack_for_state(state, mission)
    selected = select_action(state, mission, pack)
    return pack.map_action_to_intent(selected, state, mission)


def build_lazy_work_item(
    state: AgentState,
    mission: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Build next lazy work_plan item from resolved step intent."""
    step = int(state.get("mission_step") or 1)
    pack = resolve_mission_pack_for_state(state, mission)
    from app.services.action_resolver import select_action

    selected = select_action(state, mission, pack)
    item = pack.map_action_to_work_item(selected, mission, step=step, state=state)
    if item is not None:
        return item

    intent = resolve_step_intent(state, mission=mission)
    return pack.map_intent_to_work_item(intent, mission, step=step)


def apply_step_artifacts_to_payload(
    state: AgentState,
    payload: dict[str, Any],
    *,
    mission: dict[str, Any],
    intent: dict[str, Any],
) -> dict[str, Any]:
    """Attach domain artifact names and step metadata to payload."""
    pack = resolve_mission_pack_for_state(state, mission)
    policy = mission.get("step_policy") or {}
    names = pack.resolve_artifact_names(policy, mission=mission)
    out = dict(payload)
    out["execution_intent"] = intent
    if kind_writes_artifact(mission):
        out["writing_intent"] = intent
    for key, value in names.items():
        out[key] = value
    return out


def kind_writes_artifact(mission: dict[str, Any]) -> bool:
    return str(mission.get("kind") or "") == "writing"


def apply_long_running_step_to_payload(state: AgentState) -> dict[str, Any]:
    """Generic mission step payload merge (non-writing or writing via pack)."""
    payload = dict(state.get("input_payload") or {})
    mission = state.get("mission") or {}
    kind = str(mission.get("kind") or "")
    if kind == "writing":
        from app.services.mission_schema import apply_mission_step_to_payload

        return apply_mission_step_to_payload(state)

    from app.services.mission_intervention import (
        apply_intervention_to_payload,
        intervention_from_payload,
    )

    intervention = intervention_from_payload(payload)
    if intervention:
        payload = apply_intervention_to_payload(payload, intervention)

    intent = resolve_step_intent(merge_state(state, input_payload=payload), mission=mission)
    payload = apply_step_artifacts_to_payload(state, payload, mission=mission, intent=intent)
    payload["skip_planning_llm"] = True
    return payload
