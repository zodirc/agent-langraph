"""
Long-horizon mission orchestration — short work items, stepwise execution.

Work plans are supplied explicitly (API / planning tools) or grown lazily from
step_policy (one item per pause), not pre-computed with hard-coded chapter counts.
"""

from __future__ import annotations

from typing import Any, Optional

from app.config.settings import settings
from app.domain.mission import StepPolicy
from app.runtime.state import AgentState, merge_state


def orchestration_config(mission: dict[str, Any]) -> dict[str, Any]:
    raw = mission.get("orchestration")
    if isinstance(raw, dict):
        return raw
    return {}


def orchestration_enabled(mission: dict[str, Any]) -> bool:
    cfg = orchestration_config(mission)
    if "enabled" in cfg:
        return bool(cfg["enabled"])
    if str(mission.get("kind")) != "writing":
        return False
    target = float((mission.get("success_criteria") or {}).get("target") or 0)
    min_chars = int(getattr(settings, "MISSION_ORCHESTRATION_MIN_CHARS", 8000))
    return target >= min_chars


def mission_is_autonomous(mission: dict[str, Any]) -> bool:
    if str(mission.get("execution_mode", "")).lower() == "autonomous":
        return True
    return bool((mission.get("constraints") or {}).get("no_human"))


def stepwise_pause(mission: dict[str, Any]) -> bool:
    cfg = orchestration_config(mission)
    if "stepwise" in cfg:
        return bool(cfg["stepwise"])
    if mission_is_autonomous(mission):
        return False
    return orchestration_enabled(mission)


def work_plan_from_mission(mission: dict[str, Any]) -> dict[str, Any]:
    """Empty lazy plan, or explicit plan from mission.work_plan."""
    explicit = mission.get("work_plan")
    if isinstance(explicit, dict) and isinstance(explicit.get("items"), list):
        items = [
            {
                **item,
                "status": str(item.get("status") or "pending"),
            }
            for item in explicit["items"]
            if isinstance(item, dict)
        ]
        return {
            "version": int(explicit.get("version") or 1),
            "mode": str(explicit.get("mode") or "explicit"),
            "items": items,
            "current_id": explicit.get("current_id"),
            "completed_ids": list(explicit.get("completed_ids") or []),
            "total_items": len(items),
        }
    return {
        "version": 1,
        "mode": "lazy",
        "items": [],
        "current_id": None,
        "completed_ids": [],
        "total_items": 0,
    }


def append_work_items(plan: dict[str, Any], new_items: list[dict[str, Any]]) -> dict[str, Any]:
    items = list(plan.get("items") or [])
    items.extend(new_items)
    return {
        **plan,
        "items": items,
        "total_items": len(items),
    }


def build_next_lazy_work_item(state: AgentState, mission: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Derive a single next item from step_policy + manuscript (mechanical, not NLP)."""
    from app.services.mission_schema import resolve_writing_intent_for_step

    step = int(state.get("mission_step") or 1)
    intent = resolve_writing_intent_for_step(state, mission=mission)
    if not intent.get("enabled"):
        action = str(intent.get("action") or "")
        if action == "edit_plot":
            return {
                "id": f"wi-step-{step}",
                "kind": "edit_plot",
                "title": "edit_plot",
                "status": "pending",
                "params": {"edit_spec": intent.get("edit_spec") or {}},
            }
        if action == "human_gate":
            return {
                "id": f"wi-gate-{step}",
                "kind": "human_gate",
                "title": "human_gate",
                "status": "pending",
                "params": {},
            }
        return None

    action = str(intent.get("action") or "append_body")
    policy = StepPolicy.from_dict(mission.get("step_policy") or {})
    if action == "write_outline":
        kind = "write_outline"
        title = "write_outline"
    elif action == "write_body":
        kind = "write_body"
        title = "write_body"
    else:
        kind = "append_chapter"
        title = f"append chapter {intent.get('chapter_index', '?')}"

    return {
        "id": f"wi-step-{step}",
        "kind": kind,
        "title": title,
        "status": "pending",
        "params": {
            "target_chars": intent.get("target_chars") or policy.chars_per_step,
            "chapter_index": intent.get("chapter_index"),
            "require_read_first": intent.get("require_read_first"),
        },
    }


def ensure_work_plan(state: AgentState) -> AgentState:
    mission = state.get("mission") or {}
    if not orchestration_enabled(mission):
        return state
    progress = dict(state.get("progress") or {})
    if progress.get("work_plan"):
        return state
    plan = work_plan_from_mission(mission)
    progress["work_plan"] = plan
    progress["phase"] = progress.get("phase") or "orchestrating"
    orch = dict(orchestration_config(mission))
    orch.setdefault("enabled", True)
    orch.setdefault("stepwise", True)
    mission = {**mission, "orchestration": orch}
    return merge_state(state, mission=mission, progress=progress)


def ensure_next_work_item(state: AgentState) -> AgentState:
    """In lazy mode, append one step_policy-derived item when the queue is empty."""
    mission = state.get("mission") or {}
    if not orchestration_enabled(mission):
        return state
    plan = dict(_plan(state))
    if plan.get("mode") != "lazy":
        return state
    if _has_pending_items(plan):
        return state
    item = build_next_lazy_work_item(state, mission)
    if not item:
        return state
    progress = dict(state.get("progress") or {})
    progress["work_plan"] = append_work_items(plan, [item])
    return merge_state(state, progress=progress)


def _plan(state: AgentState) -> dict[str, Any]:
    return dict((state.get("progress") or {}).get("work_plan") or {})


def _has_pending_items(plan: dict[str, Any]) -> bool:
    return any(i.get("status") == "pending" for i in (plan.get("items") or []))


def get_current_work_item(state: AgentState) -> Optional[dict[str, Any]]:
    plan = _plan(state)
    items = plan.get("items") or []
    if not items:
        return None
    current_id = plan.get("current_id")
    if current_id:
        for item in items:
            if item.get("id") == current_id and item.get("status") != "done":
                return item
    for item in items:
        if item.get("status") == "pending":
            return item
    return None


def activate_work_item(state: AgentState, item: dict[str, Any]) -> AgentState:
    plan = dict(_plan(state))
    items = list(plan.get("items") or [])
    for idx, row in enumerate(items):
        if row.get("id") == item.get("id"):
            items[idx] = {**row, "status": "running"}
            break
    plan["items"] = items
    plan["current_id"] = item.get("id")
    progress = dict(state.get("progress") or {})
    progress["work_plan"] = plan
    return merge_state(state, progress=progress)


def complete_current_work_item(state: AgentState) -> AgentState:
    plan = dict(_plan(state))
    current_id = plan.get("current_id")
    if not current_id:
        return state
    items = list(plan.get("items") or [])
    completed = list(plan.get("completed_ids") or [])
    for idx, row in enumerate(items):
        if row.get("id") == current_id:
            items[idx] = {**row, "status": "done"}
            if current_id not in completed:
                completed.append(current_id)
            break
    plan["items"] = items
    plan["completed_ids"] = completed
    plan["current_id"] = None
    progress = dict(state.get("progress") or {})
    progress["work_plan"] = plan
    progress["steps_completed"] = len(completed)
    return merge_state(state, progress=progress)


def insert_work_item_after_current(
    state: AgentState,
    item: dict[str, Any],
) -> AgentState:
    plan = dict(_plan(state))
    items = list(plan.get("items") or [])
    current_id = plan.get("current_id")
    insert_at = len(items)
    if current_id:
        for i, row in enumerate(items):
            if row.get("id") == current_id:
                insert_at = i + 1
                break
    items.insert(insert_at, {**item, "status": "pending"})
    plan["items"] = items
    plan["total_items"] = len(items)
    plan["current_id"] = None
    plan["mode"] = plan.get("mode") or "lazy"
    progress = dict(state.get("progress") or {})
    progress["work_plan"] = plan
    return merge_state(state, progress=progress)


def work_item_to_writing_intent(
    item: dict[str, Any],
    *,
    mission: dict[str, Any],
    mission_step: int,
) -> dict[str, Any]:
    kind = str(item.get("kind") or "")
    params = dict(item.get("params") or {})
    policy = StepPolicy.from_dict(mission.get("step_policy") or {})
    base = {
        "enabled": True,
        "source": "work_plan",
        "mission_step": mission_step,
        "work_item_id": item.get("id"),
        "work_item_title": item.get("title"),
    }

    if kind == "write_outline":
        return {
            **base,
            "action": "write_outline",
            "target_chars": int(params.get("target_chars") or policy.outline_max_chars),
            "min_chars": int(getattr(settings, "MANUSCRIPT_MIN_OUTLINE_CHARS", 80)),
            "require_read_first": bool(params.get("require_read_first")),
        }
    if kind in ("append_chapter", "append_body"):
        return {
            **base,
            "action": "append_body",
            "target_chars": int(params.get("target_chars") or policy.chars_per_step),
            "min_chars": int(getattr(settings, "MANUSCRIPT_MIN_BODY_CHARS", 200)),
            "chapter_index": int(params.get("chapter_index") or 1),
        }
    if kind == "write_body":
        return {
            **base,
            "action": "write_body",
            "target_chars": int(params.get("target_chars") or policy.chars_per_step),
            "min_chars": int(getattr(settings, "MANUSCRIPT_MIN_BODY_CHARS", 200)),
            "chapter_index": int(params.get("chapter_index") or 1),
        }
    if kind == "reset_body":
        return {
            **base,
            "action": "reset_body",
            "target_chars": int(params.get("target_chars") or policy.chars_per_step),
            "min_chars": int(getattr(settings, "MANUSCRIPT_MIN_BODY_CHARS", 200)),
            "chapter_index": 1,
            "require_read_first": bool(params.get("require_read_first", True)),
        }
    if kind == "edit_plot":
        return {
            **base,
            "enabled": False,
            "action": "edit_plot",
            "edit_spec": params.get("edit_spec") or {},
        }
    if kind == "run_tools":
        return {**base, "enabled": False, "action": "run_tools"}
    if kind == "human_gate":
        return {**base, "enabled": False, "action": "human_gate"}
    if kind in (
        "consistency_check",
        "review_chapter",
        "polish_chapter",
        "chapter_summary",
        "arc_checkpoint",
    ):
        return {
            **base,
            "action": kind,
            "chapter_index": int(params.get("chapter_index") or 1),
        }
    return {**base, "enabled": False, "action": kind}


def _apply_tools_for_edit_plot(payload: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload)
    payload["selected_tools"] = ["read_text_artifact", "edit_text_artifact"]
    payload.setdefault("tool_params", {})
    payload["tool_params"]["read_text_artifact"] = {
        "filename": spec.get("filename", "novel.txt"),
        "max_chars": int(spec.get("read_max_chars", 12000)),
    }
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
        payload["tool_params"]["edit_text_artifact"] = edit_params
    return payload


def apply_work_plan_to_payload(state: AgentState) -> AgentState:
    mission = state.get("mission") or {}
    if not orchestration_enabled(mission):
        return state

    from app.services.mission_intervention import (
        intervention_from_payload,
        intervention_to_writing_intent,
        is_forced,
    )

    state = ensure_next_work_item(state)
    payload = dict(state.get("input_payload") or {})
    intervention = intervention_from_payload(payload)
    if intervention and is_forced(intervention):
        step = int(state.get("mission_step") or 1)
        payload = dict(state.get("input_payload") or {})
        from app.services.mission_intervention import apply_intervention_to_payload

        payload = apply_intervention_to_payload(payload, intervention)
        wi = intervention.get("work_item")
        if wi:
            state = insert_work_item_after_current(
                state,
                {
                    "id": str(wi.get("id") or "wi-forced"),
                    "kind": str(wi.get("kind") or intervention["action"]),
                    "title": str(wi.get("title") or "forced"),
                    "status": "pending",
                    "params": dict(wi.get("params") or {}),
                },
            )
        intent = intervention_to_writing_intent(intervention, mission_step=step)
        payload["writing_intent"] = intent
        if intervention["action"] == "edit_plot":
            payload = _apply_tools_for_edit_plot(
                payload, intervention.get("edit_spec") or {}
            )
        return merge_state(state, input_payload=payload)

    item = get_current_work_item(state)
    if not item:
        return state
    state = activate_work_item(state, item)
    payload = dict(state.get("input_payload") or {})
    step = int(state.get("mission_step") or 1)
    intent = work_item_to_writing_intent(item, mission=mission, mission_step=step)
    payload["writing_intent"] = intent
    payload["current_work_item"] = item
    if str(item.get("kind")) == "edit_plot":
        payload = _apply_tools_for_edit_plot(payload, dict(intent.get("edit_spec") or {}))
    if str(item.get("kind")) == "run_tools":
        tools = (item.get("params") or {}).get("tools") or [
            "read_text_artifact",
            "edit_text_artifact",
        ]
        payload["selected_tools"] = list(tools)
        payload["tool_params"] = {
            **payload.get("tool_params", {}),
            **((item.get("params") or {}).get("tool_params") or {}),
        }
    return merge_state(state, input_payload=payload)


def pending_work_items(state: AgentState) -> int:
    plan = _plan(state)
    return sum(1 for i in (plan.get("items") or []) if i.get("status") == "pending")


def work_plan_completed(state: AgentState) -> bool:
    mission = state.get("mission") or {}
    plan = _plan(state)
    items = plan.get("items") or []
    if plan.get("mode") == "lazy":
        from app.domain.packs.registry import get_domain_pack

        try:
            pack = get_domain_pack(str(mission.get("kind", "writing")))
            success, _ = pack.evaluate_success(
                mission,
                state.get("progress") or {},
                state.get("observation") or {},
            )
            return success
        except KeyError:
            return False
    return bool(items) and all(i.get("status") == "done" for i in items)


def _work_item_label(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "").strip()
    kind = str(item.get("kind") or "").strip()
    return title or kind or "?"


def orchestration_detail(state: AgentState) -> dict[str, Any]:
    """Structured work-plan progress for UI / SSE (avoids ambiguous 1/2 + current)."""
    plan = _plan(state)
    items = list(plan.get("items") or [])
    done_items = [i for i in items if i.get("status") == "done"]
    current = get_current_work_item(state)
    completed_labels = [_work_item_label(i) for i in done_items]
    cur_label = _work_item_label(current) if current else None
    cur_status = str((current or {}).get("status") or "") if current else None
    return {
        "mode": plan.get("mode", "?"),
        "done": len(done_items),
        "total": len(items),
        "completed": completed_labels,
        "current_kind": (current or {}).get("kind"),
        "current_title": cur_label,
        "current_status": cur_status or None,
    }


def orchestration_summary(state: AgentState) -> str:
    detail = orchestration_detail(state)
    mode = detail["mode"]
    done = detail["done"]
    total = detail["total"]
    completed = "、".join(detail["completed"]) if detail["completed"] else "无"
    current = detail.get("current_title")
    cur_status = detail.get("current_status")
    if current:
        status_note = f"（{cur_status}）" if cur_status and cur_status != "done" else ""
        current_part = f"当前：{current}{status_note}"
    else:
        current_part = "当前：无（待下一工作项）"
    return f"编排({mode}) 已完成 {done}/{total}：{completed}；{current_part}"
