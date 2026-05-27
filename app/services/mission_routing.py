"""
Mission routing — explicit contract wins; planning LLM may auto-enable when allowed.
"""

from __future__ import annotations

from typing import Any, Optional

from app.config.settings import settings


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def explicit_mission_requested(payload: dict[str, Any], execution_mode: str = "") -> bool:
    """User/API/Web already supplied a mission contract or mission execution mode."""
    mode = str(execution_mode or payload.get("execution_mode", "")).lower()
    if mode in ("mission", "autonomous", "longform"):
        return True
    block = payload.get("mission")
    if isinstance(block, dict) and block:
        return True
    if str(payload.get("mission_kind", "")).strip():
        return True
    return False


def mission_auto_allowed(payload: dict[str, Any]) -> bool:
    """Planning model may propose mission unless user opted out."""
    if not getattr(settings, "MISSION_AUTO_FROM_PLANNING", True):
        return False
    if _coerce_bool(payload.get("force_single_turn")):
        return False
    if payload.get("mission_auto") is False:
        return False
    if _coerce_bool(payload.get("disable_mission_auto")):
        return False
    return True


def _default_step_policy() -> dict[str, Any]:
    return {
        "first_step": "outline",
        "then": "append_body",
        "chars_per_step": int(getattr(settings, "MISSION_CHARS_PER_STEP", 4000)),
    }


def _merge_mission_block(block: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    goal = str(payload.get("goal") or block.get("objective") or "").strip()
    merged = dict(block)
    merged.setdefault("kind", "writing")
    merged.setdefault("objective", goal)
    merged.setdefault("autonomous", True)
    if not merged.get("step_policy"):
        merged["step_policy"] = _default_step_policy()
    if not merged.get("total_target_chars"):
        wi = payload.get("writing_intent") if isinstance(payload.get("writing_intent"), dict) else {}
        total = merged.get("total_target_chars") or (wi or {}).get("target_chars")
        if total:
            merged["total_target_chars"] = int(total)
    return merged


def normalize_planning_mission(
    planning_result: dict[str, Any],
    payload: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """
    Build mission block from planning LLM JSON when the model chose long-horizon mode.

    Accepts:
    - ``mission``: { kind, total_target_chars, step_policy, ... }
    - ``mission_recommended`` / ``use_mission`` + ``total_target_chars`` (or large target in mission)
    """
    if not isinstance(planning_result, dict):
        return None

    block = planning_result.get("mission")
    if isinstance(block, dict) and block:
        kind = str(block.get("kind") or "writing").lower()
        if kind == "writing" or block.get("total_target_chars"):
            return _merge_mission_block(block, payload)

    recommended = _coerce_bool(planning_result.get("mission_recommended")) or _coerce_bool(
        planning_result.get("use_mission")
    )
    if not recommended:
        return None

    total_raw = planning_result.get("total_target_chars")
    if total_raw is None and isinstance(block, dict):
        total_raw = block.get("total_target_chars")
    if total_raw is None:
        wi = planning_result.get("writing_intent")
        if isinstance(wi, dict) and not _coerce_bool(wi.get("enabled")):
            total_raw = wi.get("target_chars")
    try:
        total = int(total_raw) if total_raw is not None else 0
    except (TypeError, ValueError):
        total = 0

    min_total = int(getattr(settings, "MISSION_AUTO_MIN_TOTAL_CHARS", 50000))
    if total < min_total:
        return None

    goal = str(payload.get("goal") or "").strip()
    return _merge_mission_block(
        {
            "kind": "writing",
            "objective": goal,
            "total_target_chars": total,
            "autonomous": True,
            "step_policy": planning_result.get("step_policy") or _default_step_policy(),
        },
        payload,
    )


def patch_mission_from_planning(
    planning_result: dict[str, Any],
    payload: dict[str, Any],
    *,
    state_mission: Optional[dict[str, Any]] = None,
) -> tuple[dict[str, Any], Optional[str]]:
    """
    Update an existing writing mission from planning output (e.g. steer sets 120W total).
    Does not require mission_auto / empty mission — patches totals and step_policy in place.
    """
    if not isinstance(planning_result, dict):
        return payload, None

    mission = dict(state_mission or payload.get("mission") or {})
    if str(mission.get("kind", "")).lower() != "writing":
        return payload, None

    block = planning_result.get("mission")
    plan_block = block if isinstance(block, dict) else {}

    changes: dict[str, Any] = {}
    total_raw = planning_result.get("total_target_chars")
    if total_raw is None:
        total_raw = plan_block.get("total_target_chars")

    if total_raw is not None:
        try:
            total = int(total_raw)
        except (TypeError, ValueError):
            total = 0
        if total > 0:
            prev = int(
                mission.get("total_target_chars")
                or (mission.get("success_criteria") or {}).get("target")
                or 0
            )
            if total != prev:
                changes["total_target_chars"] = total

    step_policy = planning_result.get("step_policy") or plan_block.get("step_policy")
    if isinstance(step_policy, dict) and step_policy:
        merged_sp = {**(mission.get("step_policy") or {}), **step_policy}
        if merged_sp != (mission.get("step_policy") or {}):
            changes["step_policy"] = merged_sp

    budget = planning_result.get("budget") or plan_block.get("budget")
    if isinstance(budget, dict) and budget:
        merged_budget = {**(mission.get("budget") or {}), **budget}
        if merged_budget != (mission.get("budget") or {}):
            changes["budget"] = merged_budget

    objective = plan_block.get("objective")
    if objective and str(objective).strip():
        changes["objective"] = str(objective).strip()

    if not changes:
        return payload, None

    mission = {**mission, **changes}
    if "total_target_chars" in changes:
        sc = dict(mission.get("success_criteria") or {})
        sc.update(
            {
                "type": "metric_gte",
                "metric": "written_chars",
                "target": float(changes["total_target_chars"]),
                "evidence_source": sc.get("evidence_source") or "progress",
            }
        )
        mission["success_criteria"] = sc

    payload = {**payload, "mission": mission}
    return payload, "planning_mission_patch"


def apply_planning_mission_decision(
    planning_result: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[dict[str, Any], Optional[str]]:
    """
    Merge auto-detected mission into payload. Returns (payload, reason_or_none).

    Skips when explicit mission already present or auto routing disabled.
    """
    payload = dict(payload)
    if explicit_mission_requested(payload):
        return payload, None
    if not mission_auto_allowed(payload):
        return payload, None

    block = normalize_planning_mission(planning_result, payload)
    if not block:
        return payload, None

    payload["mission"] = block
    if not str(payload.get("execution_mode") or "").strip():
        payload["execution_mode"] = "mission"
    wi = payload.get("writing_intent")
    if isinstance(wi, dict):
        payload["writing_intent"] = {**wi, "enabled": False}
    return payload, "planning_auto_mission"


def should_use_mission_runtime(payload: dict[str, Any], execution_mode: str = "") -> bool:
    """Mission graph when explicit contract exists or planning merged mission into payload."""
    return explicit_mission_requested(payload, execution_mode)
