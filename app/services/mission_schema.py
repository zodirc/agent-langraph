"""
Mission schema — explicit contract from payload (no NLP regex for word counts).
"""

from __future__ import annotations

from typing import Any, Optional

from app.config.settings import settings
from app.domain.mission import (
    MISSION_SCHEMA_VERSION,
    Mission,
    MissionBudget,
    StepPolicy,
    SuccessCriteria,
)
from app.runtime.state import AgentState, merge_state
from app.services.manuscript_service import manuscript_has_body, resolve_manuscript


def mission_block_from_payload(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    block = payload.get("mission")
    if isinstance(block, dict) and block:
        return block
    return None


def should_use_mission_runtime(payload: dict[str, Any], execution_mode: str = "") -> bool:
    """Mission graph when explicit or planning-auto mission contract is in payload."""
    from app.services.mission_routing import should_use_mission_runtime as _route

    return _route(payload, execution_mode)


def coerce_step_policy(block: dict[str, Any]) -> StepPolicy:
    """Build StepPolicy from explicit mission fields only."""
    sp = block.get("step_policy")
    if isinstance(sp, dict):
        return StepPolicy.from_dict(sp)

    return StepPolicy(
        unit=str(block.get("unit", "chapter")),
        chars_per_step=int(
            block.get("chars_per_step")
            or block.get("per_step_chars")
            or settings.ARTIFACT_CHUNK_CHARS
        ),
        outline_max_chars=int(
            block.get("outline_max_chars")
            or getattr(settings, "MISSION_OUTLINE_MAX_CHARS", 12000)
        ),
        first_step=str(block.get("first_step", "outline")),
        then=str(block.get("then", "append_body")),
        body_artifact=str(
            block.get("body_artifact")
            or block.get("artifact_path")
            or settings.MANUSCRIPT_DEFAULT_BODY
        ),
        outline_artifact=str(
            block.get("outline_artifact") or settings.MANUSCRIPT_DEFAULT_OUTLINE
        ),
    )


def coerce_success_criteria(block: dict[str, Any], kind: str) -> SuccessCriteria:
    sc = block.get("success_criteria")
    if isinstance(sc, dict):
        return SuccessCriteria.from_dict(sc)

    total = block.get("total_target_chars") or block.get("target_chars")
    if total is not None and kind == "writing":
        return SuccessCriteria(
            type="metric_gte",
            metric="written_chars",
            target=float(int(total)),
            evidence_source="progress",
        )
    return SuccessCriteria(type="steps_done", target=1)


def mission_steps_hard_cap() -> int:
    return int(getattr(settings, "MISSION_STEPS_HARD_CAP", 500))


def estimate_writing_max_steps(block: dict[str, Any]) -> int:
    """
    Derive a step budget from total_target_chars + step_policy when the planner
    did not set budget.max_steps (mechanical fallback, not NLP).
    """
    cap = mission_steps_hard_cap()
    total_raw = block.get("total_target_chars") or block.get("target_chars")
    try:
        total = int(total_raw) if total_raw is not None else 0
    except (TypeError, ValueError):
        total = 0
    if total <= 0:
        return cap

    sp = block.get("step_policy") if isinstance(block.get("step_policy"), dict) else {}
    try:
        per = int(
            sp.get("chars_per_step")
            or block.get("chars_per_step")
            or getattr(settings, "MISSION_CHARS_PER_STEP", 4000)
        )
    except (TypeError, ValueError):
        per = int(getattr(settings, "MISSION_CHARS_PER_STEP", 4000))
    per = max(per, 1)
    first = str(sp.get("first_step") or block.get("first_step") or "outline").lower()
    body_steps = (total + per - 1) // per
    outline_extra = 1 if first in ("outline", "write_outline") else 0
    return min(outline_extra + body_steps, cap)


def resolve_mission_budget_dict(block: dict[str, Any], *, kind: str) -> dict[str, Any]:
    """
    Resolve mission budget.max_steps: planner value (capped), else estimate for writing,
    else global default (hard cap 500).
    """
    cap = mission_steps_hard_cap()
    budget_in = block.get("budget") if isinstance(block.get("budget"), dict) else {}
    explicit = budget_in.get("max_steps")
    if explicit is None:
        explicit = block.get("max_steps")

    if explicit is not None:
        try:
            max_steps = min(max(1, int(explicit)), cap)
        except (TypeError, ValueError):
            max_steps = estimate_writing_max_steps(block) if kind == "writing" else cap
    elif kind == "writing":
        max_steps = estimate_writing_max_steps(block)
    else:
        max_steps = min(
            int(getattr(settings, "MISSION_MAX_STEPS", cap)),
            cap,
        )

    default_wall = int(getattr(settings, "MISSION_MAX_WALL_SEC", 3600))
    default_failures = int(getattr(settings, "MISSION_MAX_FAILURES", 3))
    try:
        max_wall = int(budget_in.get("max_wall_sec") or block.get("max_wall_sec") or default_wall)
    except (TypeError, ValueError):
        max_wall = default_wall
    try:
        max_failures = int(
            budget_in.get("max_failures") or block.get("max_failures") or default_failures
        )
    except (TypeError, ValueError):
        max_failures = default_failures

    return {
        "max_steps": max_steps,
        "max_wall_sec": max_wall,
        "max_failures": max_failures,
    }


def build_mission_dict(
    state: AgentState,
    payload: dict[str, Any],
    *,
    kind: str,
    mission_block: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Assemble versioned mission contract from explicit API/planning fields."""
    block = dict(mission_block or mission_block_from_payload(payload) or {})
    goal = str(payload.get("goal") or block.get("objective") or "").strip()

    autonomous = bool(
        block.get("autonomous")
        or block.get("no_human")
        or str(block.get("execution_mode", "")).lower() == "autonomous"
        or str(payload.get("execution_mode", "")).lower() in ("mission", "autonomous")
    )

    orch_block = block.get("orchestration") if isinstance(block.get("orchestration"), dict) else None

    mission = Mission(
        id=state["task_id"],
        kind=kind,
        objective=str(block.get("objective") or goal),
        success_criteria=coerce_success_criteria(block, kind),
        constraints={
            **dict(block.get("constraints") or {}),
            "no_human": autonomous,
        },
        execution_mode="autonomous" if autonomous else str(
            block.get("execution_mode", "interactive")
        ),
        orchestration=orch_block,
        budget=MissionBudget.from_dict(resolve_mission_budget_dict(block, kind=kind)),
        step_policy=coerce_step_policy(block) if kind == "writing" else None,
        schema_version=str(block.get("schema_version", MISSION_SCHEMA_VERSION)),
    )
    return mission.to_dict()


def resolve_writing_intent_for_step(
    state: AgentState,
    *,
    mission: dict[str, Any],
) -> dict[str, Any]:
    """
    Derive writing_intent from step_policy + manuscript, or explicit forced intervention only.
    """
    from app.services.mission_intervention import (
        intervention_from_payload,
        intervention_to_writing_intent,
        is_forced,
    )

    policy = StepPolicy.from_dict(mission.get("step_policy") or {})
    ms = resolve_manuscript(state["task_id"], state.get("manuscript"))
    step = int(state.get("mission_step") or 1)
    payload = state.get("input_payload") or {}

    intervention = intervention_from_payload(payload)
    if intervention and is_forced(intervention):
        intent = intervention_to_writing_intent(intervention, mission_step=step)
        if intent.get("action") == "write_outline":
            intent.setdefault("target_chars", policy.outline_max_chars)
        elif intent.get("action") in ("write_body", "append_body", "reset_body"):
            intent.setdefault("target_chars", policy.chars_per_step)
        return intent

    action = (intervention or {}).get("action") if intervention else None

    ms = resolve_manuscript(state["task_id"], ms.to_dict())
    has_outline = bool(ms.outline_path) and int(ms.outline_bytes or 0) > 0
    has_body = manuscript_has_body(ms)

    if action == "reset_body":
        return {
            "enabled": True,
            "action": "reset_body",
            "target_chars": policy.chars_per_step,
            "min_chars": int(getattr(settings, "MANUSCRIPT_MIN_BODY_CHARS", 200)),
            "source": "mission_intervention",
            "mission_step": step,
            "chapter_index": 1,
            "revision": ms.revision,
            "require_read_first": True,
        }

    if action == "edit_plot":
        spec = dict(payload.get("edit_plot_spec") or {})
        return {
            "enabled": False,
            "action": "edit_plot",
            "source": "mission_intervention",
            "mission_step": step,
            "edit_spec": spec,
        }

    if action == "rewrite_outline":
        return {
            "enabled": True,
            "action": "write_outline",
            "target_chars": policy.outline_max_chars,
            "min_chars": int(getattr(settings, "MANUSCRIPT_MIN_OUTLINE_CHARS", 80)),
            "source": "mission_intervention",
            "mission_step": step,
            "revision": ms.revision,
            "require_read_first": True,
        }

    if action == "review_outline":
        return {
            "enabled": False,
            "action": "review_outline",
            "source": "mission_intervention",
            "mission_step": step,
        }

    if policy.first_step == "outline" and not has_outline:
        return {
            "enabled": True,
            "action": "write_outline",
            "target_chars": policy.outline_max_chars,
            "min_chars": int(getattr(settings, "MANUSCRIPT_MIN_OUTLINE_CHARS", 80)),
            "source": "mission_step_policy",
            "mission_step": step,
        }

    from app.services.manuscript_context import parse_last_chapter_index, read_body_text

    last_ch = 0
    if has_body and ms.body_path:
        last_ch = parse_last_chapter_index(
            read_body_text(state["task_id"], ms.body_path, state=state)
        )

    if not has_body:
        min_body = int(getattr(settings, "MANUSCRIPT_MIN_BODY_CHARS", 200))
        return {
            "enabled": True,
            "action": "write_body",
            "target_chars": policy.chars_per_step,
            "min_chars": min_body,
            "source": "mission_step_policy",
            "mission_step": step,
            "chapter_index": 1,
            "require_read_first": False,
        }

    return {
        "enabled": True,
        "action": policy.then if policy.then in ("append_body", "write_body") else "append_body",
        "target_chars": policy.chars_per_step,
        "min_chars": int(getattr(settings, "MANUSCRIPT_MIN_BODY_CHARS", 200)),
        "source": "mission_step_policy",
        "mission_step": step,
        "chapter_index": max(1, last_ch + 1),
    }


def apply_mission_step_to_payload(state: AgentState) -> dict[str, Any]:
    """Merge mission-derived writing_intent and artifact names into input_payload."""
    from app.services.mission_intervention import (
        apply_intervention_to_payload,
        intervention_from_payload,
        is_forced,
    )

    payload = dict(state.get("input_payload") or {})
    mission = state.get("mission") or {}
    if mission.get("kind") != "writing":
        return payload

    policy = StepPolicy.from_dict(mission.get("step_policy") or {})
    intervention = intervention_from_payload(payload)
    if intervention:
        payload = apply_intervention_to_payload(payload, intervention)

    intent = resolve_writing_intent_for_step(
        merge_state(state, input_payload=payload), mission=mission
    )
    payload["writing_intent"] = intent
    payload["novel_filename"] = policy.body_artifact
    payload["outline_filename"] = policy.outline_artifact
    payload["requested_total_chars"] = (mission.get("success_criteria") or {}).get("target")
    payload["chars_per_step"] = policy.chars_per_step

    payload["skip_planning_llm"] = True
    if intervention and (
        is_forced(intervention)
        or intervention.get("action") in ("run_tools", "edit_plot")
        or intervention.get("use_planning")
    ):
        payload["skip_planning_llm"] = False
    if payload.get("steer_applied_at") and not (intervention and is_forced(intervention)):
        payload["skip_planning_llm"] = False
    from app.services.mission_steer import steer_requires_planning

    if steer_requires_planning(payload):
        payload["skip_planning_llm"] = False
    return payload
