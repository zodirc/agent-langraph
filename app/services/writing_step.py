"""Mission writing step — one orchestration action, many executor ops."""

from __future__ import annotations

from typing import Any, Optional

ADVANCE_WRITING_PRIMARY_OP = "advance_writing"

_BODY_CONTINUE_ACTIONS = frozenset({"append_body", "append_chapter", "write_body"})
_LLM_EXECUTOR_ACTIONS = frozenset(
    {
        "write_outline",
        "write_body",
        "append_body",
        "append_chapter",
        "reset_body",
        "rewrite_outline",
        "polish_chapter",
        "review_chapter",
        "chapter_summary",
    }
)


def normalize_writing_mission(mission: Any) -> Optional[dict[str, Any]]:
    """Ensure long-horizon planner blocks keep kind=writing for routing."""
    if not isinstance(mission, dict) or not mission:
        return None
    out = dict(mission)
    if (
        out.get("step_policy")
        or out.get("total_target_chars")
        or out.get("autonomous") is not None
        or out.get("mission_recommended")
    ):
        out.setdefault("kind", "writing")
    kind = str(out.get("kind") or "").strip().lower()
    if kind == "writing":
        return out
    return None


def writing_mission_from_payload(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    return normalize_writing_mission(payload.get("mission"))


def materialize_writing_step_intent(
    state: dict[str, Any],
    mission: dict[str, Any],
) -> dict[str, Any]:
    """Single entry: snapshot → concrete write_outline / write_body / append_body."""
    from app.runtime.state import merge_state
    from app.services.mission.step_reconcile import reconcile_writing_intent
    from app.services.mission_schema import resolve_writing_intent_for_step

    merged = merge_state(
        state,
        mission=mission,
        input_payload={**(state.get("input_payload") or {}), "mission": mission},
    )
    intent = resolve_writing_intent_for_step(merged, mission=mission)
    return reconcile_writing_intent(merged, intent)


def should_delegate_planning_writing_to_mission(
    *,
    mission_block: Optional[dict[str, Any]],
    payload: Optional[dict[str, Any]] = None,
) -> bool:
    """Planning must not bind LLM writing_action when mission owns the step."""
    from app.services.mission_schema import should_use_mission_runtime

    mission = normalize_writing_mission(mission_block) or (
        writing_mission_from_payload(payload or {}) if payload else None
    )
    if not mission:
        return False
    return should_use_mission_runtime({"mission": mission}, "")


def ignore_llm_writing_action(
    result: dict[str, Any],
    payload: dict[str, Any],
) -> bool:
    """True when planner emitted executor ops that must not override mission step."""
    if not writing_mission_from_payload(payload):
        return False
    wi = result.get("writing_intent") if isinstance(result.get("writing_intent"), dict) else {}
    raw = str(
        wi.get("action")
        or result.get("writing_action")
        or result.get("writing_mode")
        or ""
    ).strip()
    if wi.get("enabled") and raw in _LLM_EXECUTOR_ACTIONS:
        return True
    if raw in _BODY_CONTINUE_ACTIONS and not wi.get("enabled"):
        return True
    return False


def coerce_writing_action_for_manuscript_state(
    state: dict[str, Any],
    action: str,
) -> str:
    """Executor safety net: never append without body; never write body without outline."""
    from app.config.settings import settings
    from app.services.manuscript_service import (
        artifact_bytes_on_disk,
        manuscript_has_body,
        resolve_manuscript,
    )

    act = str(action or "").strip()
    if act not in _BODY_CONTINUE_ACTIONS and act != "write_outline":
        return act

    task_id = str(state.get("task_id") or "")
    if not task_id:
        return act

    ms = resolve_manuscript(task_id, state.get("manuscript"))
    min_outline = int(getattr(settings, "MANUSCRIPT_MIN_OUTLINE_CHARS", 80))
    outline_bytes = max(
        int(ms.outline_bytes or 0),
        artifact_bytes_on_disk(task_id, ms.outline_path),
    )
    outline_ready = bool(ms.outline_path) and outline_bytes >= min_outline

    if act in _BODY_CONTINUE_ACTIONS and not manuscript_has_body(ms):
        return "write_outline" if not outline_ready else "write_body"
    if act == "write_body" and not outline_ready:
        return "write_outline"
    return act
