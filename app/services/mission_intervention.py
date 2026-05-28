"""
Explicit mission intervention — no NLP / regex inference.

Users, clients, or the planning LLM set `mission_intervention` on the payload.
When `force=true`, the control loop must honor the action before normal step_policy.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from app.config.settings import settings

InterventionAction = Literal[
    "rewrite_outline",
    "review_outline",
    "reset_body",
    "edit_plot",
    "run_tools",
    "pause",
    "continue",
    "enqueue_work",
]

_ALLOWED_ACTIONS = frozenset(
    {
        "rewrite_outline",
        "review_outline",
        "reset_body",
        "edit_plot",
        "run_tools",
        "pause",
        "continue",
        "enqueue_work",
    }
)


def intervention_from_payload(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Read explicit intervention block (mission_intervention or legacy revision_intent)."""
    block = payload.get("mission_intervention")
    if isinstance(block, dict) and block.get("action"):
        return _normalize_intervention(block)

    legacy = payload.get("revision_intent")
    if legacy in _ALLOWED_ACTIONS:
        return _normalize_intervention(
            {
                "action": legacy,
                "force": bool(payload.get("revision_force", True)),
                "edit_spec": payload.get("edit_plot_spec") or {},
                "tools": payload.get("selected_tools") or [],
                "tool_params": payload.get("tool_params") or {},
            }
        )
    return None


def _normalize_intervention(block: dict[str, Any]) -> Optional[dict[str, Any]]:
    action = str(block.get("action") or "").strip()
    if action not in _ALLOWED_ACTIONS:
        return None
    return {
        "action": action,
        "force": bool(block.get("force", False)),
        "edit_spec": dict(block.get("edit_spec") or {}),
        "tools": list(block.get("tools") or []),
        "tool_params": dict(block.get("tool_params") or {}),
        "work_item": dict(block.get("work_item") or {}) if block.get("work_item") else None,
        "reason": str(block.get("reason") or ""),
    }


def is_forced(intervention: Optional[dict[str, Any]]) -> bool:
    return bool(intervention and intervention.get("force"))


def intervention_to_writing_intent(
    intervention: dict[str, Any],
    *,
    mission_step: int,
) -> dict[str, Any]:
    """Map forced intervention to writing_intent / tool routing hints."""
    action = intervention["action"]
    base: dict[str, Any] = {
        "source": "forced_intervention",
        "mission_step": mission_step,
        "forced": True,
    }
    if action == "rewrite_outline":
        return {
            **base,
            "enabled": True,
            "action": "write_outline",
            "require_read_first": bool(intervention.get("edit_spec", {}).get("read_first", True)),
        }
    if action == "review_outline":
        return {
            **base,
            "enabled": False,
            "action": "review_outline",
        }
    if action == "reset_body":
        return {**base, "enabled": True, "action": "reset_body"}
    if action == "edit_plot":
        return {
            **base,
            "enabled": False,
            "action": "edit_plot",
            "edit_spec": intervention.get("edit_spec") or {},
        }
    if action == "run_tools":
        return {**base, "enabled": False, "action": "run_tools"}
    if action == "pause":
        return {**base, "enabled": False, "action": "pause"}
    return {**base, "enabled": False, "action": action}


def coerce_steer_intervention(
    state: dict[str, Any],
    intervention: dict[str, Any],
) -> dict[str, Any]:
    """
    When outline already exists, downgrade rewrite_outline to localized edit_plot.

    Structural only (outline bytes, edit_spec anchors) — not user-message regex.
    """
    if str(intervention.get("action") or "") != "rewrite_outline":
        return intervention

    from app.services.manuscript_service import resolve_manuscript

    task_id = str(state.get("task_id") or "")
    stored = state.get("manuscript") or {}
    ms = resolve_manuscript(task_id, stored) if task_id else None
    outline_bytes = max(
        int(getattr(ms, "outline_bytes", 0) or 0),
        int(stored.get("outline_bytes") or 0),
    )
    outline_path = (
        (getattr(ms, "outline_path", None) if ms else None)
        or stored.get("outline_path")
        or getattr(settings, "MANUSCRIPT_DEFAULT_OUTLINE", "outline.txt")
    )
    min_outline = int(getattr(settings, "MANUSCRIPT_MIN_OUTLINE_CHARS", 80))
    spec = dict(intervention.get("edit_spec") or {})

    if spec.get("old_text") and spec.get("new_text"):
        return {
            **intervention,
            "action": "edit_plot",
            "edit_spec": {**spec, "filename": spec.get("filename") or outline_path},
            "tools": list(intervention.get("tools") or ["read_text_artifact", "edit_text_artifact"]),
            "coerced_from": "rewrite_outline",
        }

    if outline_bytes < min_outline:
        return intervention

    payload = state.get("input_payload") or {}
    goal = str(payload.get("goal") or "")
    return {
        **intervention,
        "action": "edit_plot",
        "force": bool(intervention.get("force")),
        "reason": str(intervention.get("reason") or "localized outline patch"),
        "edit_spec": {
            "filename": spec.get("filename") or outline_path,
            "steer_correction": goal[-2000:] if goal else "",
            **spec,
        },
        "tools": ["read_text_artifact", "edit_text_artifact"],
        "coerced_from": "rewrite_outline",
    }


def apply_planning_intervention(
    planning_result: dict[str, Any],
    payload: dict[str, Any],
    *,
    state: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Merge mission_intervention from planning LLM output (model decides force/actions)."""
    raw = planning_result.get("mission_intervention")
    if not isinstance(raw, dict) or not raw.get("action"):
        return payload
    block = _normalize_intervention(raw)
    if not block:
        return payload
    if state is not None:
        block = coerce_steer_intervention(state, block)
    return apply_intervention_to_payload(payload, block)


def apply_intervention_to_payload(
    payload: dict[str, Any],
    intervention: dict[str, Any],
) -> dict[str, Any]:
    """Merge intervention into payload for this turn."""
    out = {**payload, "mission_intervention": intervention}
    action = intervention["action"]
    if action in ("rewrite_outline", "reset_body", "edit_plot"):
        out["steer_watch_outcome"] = True
    if action == "review_outline":
        from app.services.mission_steer import apply_review_outline_mode

        mission = out.get("mission") if isinstance(out.get("mission"), dict) else {}
        return apply_review_outline_mode(out, mission)
    if action in ("rewrite_outline", "reset_body", "edit_plot"):
        out["revision_intent"] = action  # compat with existing branches
    if action == "edit_plot":
        spec = intervention.get("edit_spec") or {}
        out["edit_plot_spec"] = spec
        outline_name = str(
            spec.get("filename")
            or getattr(settings, "MANUSCRIPT_DEFAULT_OUTLINE", "outline.txt")
        )
        from app.services.outline_steer_patch import is_outline_filename

        if intervention.get("tools"):
            out["selected_tools"] = list(intervention["tools"])
        else:
            out["selected_tools"] = ["read_text_artifact", "edit_text_artifact"]
        out.setdefault("tool_params", {})
        out["tool_params"].setdefault(
            "read_text_artifact",
            {
                "filename": outline_name if is_outline_filename(outline_name) else spec.get("filename", "novel.txt"),
                "max_chars": int(spec.get("read_max_chars", 12000)),
            },
        )
        if spec.get("old_text"):
            edit_params = {
                k: spec[k]
                for k in (
                    "filename",
                    "old_text",
                    "new_text",
                    "replace_all",
                    "occurrence_index",
                    "start_line",
                    "end_line",
                    "dry_run",
                )
                if k in spec
            }
            if edit_params:
                out["tool_params"]["edit_text_artifact"] = edit_params
        elif is_outline_filename(outline_name):
            out["outline_edit_via_tools"] = True
            out["tool_stages"] = [["read_text_artifact"], ["edit_text_artifact"]]
    if action == "run_tools" and intervention.get("tools"):
        out["selected_tools"] = list(intervention["tools"])
        out["tool_params"] = {**out.get("tool_params", {}), **intervention.get("tool_params", {})}
    if is_forced(intervention):
        out["force_slow_reasoning"] = True
        out.pop("skip_planning_llm", None)
    from app.services.turn_contract import apply_turn_contract_to_payload, build_turn_contract

    contract = build_turn_contract(
        {"mission_intervention": intervention},
        out,
        steer_planning_turn=bool(out.get("require_planning_after_steer")),
    )
    out = apply_turn_contract_to_payload(out, contract)
    return out
