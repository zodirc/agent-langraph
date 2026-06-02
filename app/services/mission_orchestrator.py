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
    from app.services.task_agenda import ensure_agenda_fields

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
        return ensure_agenda_fields(
            {
                "version": int(explicit.get("version") or 1),
                "mode": str(explicit.get("mode") or "explicit"),
                "items": items,
                "current_id": explicit.get("current_id"),
                "completed_ids": list(explicit.get("completed_ids") or []),
                "total_items": len(items),
            }
        )
    return ensure_agenda_fields(
        {
            "version": 1,
            "mode": "lazy",
            "items": [],
            "current_id": None,
            "completed_ids": [],
            "total_items": 0,
        }
    )


def append_work_items(plan: dict[str, Any], new_items: list[dict[str, Any]]) -> dict[str, Any]:
    from app.services.task_agenda import ensure_agenda_fields

    plan = ensure_agenda_fields(plan)
    items = list(plan.get("items") or [])
    last_id: Optional[str] = None
    if items:
        tail = items[-1]
        if tail.get("id"):
            last_id = str(tail["id"])
    for item in new_items:
        row = dict(item)
        if not row.get("depends_on") and last_id:
            row["depends_on"] = [last_id]
        items.append(row)
        if row.get("id"):
            last_id = str(row["id"])
    return {
        **plan,
        "items": items,
        "total_items": len(items),
    }


def build_next_lazy_work_item(state: AgentState, mission: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Derive a single next item from long_running_task runtime."""
    from app.services.long_running_task import build_lazy_work_item

    return build_lazy_work_item(state, mission)


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
    from app.services.task_agenda import ensure_agenda_fields, select_next_runnable_item

    plan = ensure_agenda_fields(plan)
    if select_next_runnable_item(plan):
        return True
    return any(
        str(i.get("status") or "") in ("pending", "running", "blocked", "failed")
        for i in (plan.get("items") or [])
    )


def get_current_work_item(state: AgentState) -> Optional[dict[str, Any]]:
    from app.services.task_agenda import ensure_agenda_fields, select_next_runnable_item

    plan = ensure_agenda_fields(_plan(state))
    items = plan.get("items") or []
    if not items:
        return None
    current_id = plan.get("current_id")
    if current_id:
        for item in items:
            if item.get("id") == current_id and str(item.get("status") or "") not in ("done",):
                return item
    return select_next_runnable_item(plan)


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


def mark_current_work_item_failed(
    state: AgentState,
    *,
    reason: str = "step_failed",
) -> AgentState:
    """Mark running/current item failed and propagate blocked dependents."""
    from app.services.task_agenda import ensure_agenda_fields, propagate_failure, set_item_status

    plan = ensure_agenda_fields(_plan(state))
    current_id = plan.get("current_id")
    if not current_id:
        item = get_current_work_item(state)
        current_id = item.get("id") if item else None
    if not current_id:
        return state
    plan = set_item_status(plan, str(current_id), "failed", reason=reason)
    plan = propagate_failure(plan, str(current_id))
    plan["current_id"] = None
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
    from app.domain.packs.registry import resolve_mission_pack

    pack = resolve_mission_pack(mission_kind=str(mission.get("kind") or "writing"))
    return pack.work_item_to_intent(item, mission=mission, mission_step=mission_step)


def _apply_tools_for_edit_plot(payload: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    from app.config.settings import settings

    outline_name = str(
        spec.get("filename")
        or getattr(settings, "MANUSCRIPT_DEFAULT_OUTLINE", "outline.txt")
    )
    payload = dict(payload)
    payload["selected_tools"] = ["read_text_artifact", "edit_text_artifact"]
    payload.setdefault("tool_params", {})
    payload["tool_params"]["read_text_artifact"] = {
        "filename": outline_name,
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


def _apply_tools_for_work_item(
    payload: dict[str, Any],
    item: dict[str, Any],
    *,
    default_body_name: str,
) -> dict[str, Any]:
    kind = str(item.get("kind") or "")
    params = dict(item.get("params") or {})
    payload = dict(payload)
    payload.setdefault("tool_params", {})

    if kind == "patch_recent_chapter":
        file_path = str(params.get("filename") or params.get("path") or default_body_name)
        tools = ["read_file", "grep_file"]
        tool_params = {
            "read_file": {
                "path": file_path,
                "max_chars": int(params.get("read_max_chars") or 12000),
            },
            "grep_file": {
                "path": file_path,
                "pattern": str(params.get("grep_pattern") or "TODO|待补|TBD"),
                "regex": True,
                "ignore_case": True,
                "max_lines": int(params.get("grep_max_lines") or 80),
            },
        }
        if params.get("old_text") is not None and params.get("new_text") is not None:
            tools.append("replace_in_file")
            tool_params["replace_in_file"] = {
                "path": file_path,
                "old_text": str(params.get("old_text")),
                "new_text": str(params.get("new_text")),
                "replace_all": bool(params.get("replace_all", False)),
                # Optional by default: preview edit intent without forcing write.
                "dry_run": bool(params.get("dry_run", True)),
            }
        payload["selected_tools"] = tools
        payload["tool_params"] = {**payload["tool_params"], **tool_params}
        return payload

    if kind in ("consistency_check", "reconcile_outline_body"):
        file_path = str(params.get("filename") or params.get("path") or default_body_name)
        payload["selected_tools"] = ["ls_path", "read_file", "grep_file"]
        payload["tool_params"] = {
            **payload["tool_params"],
            "ls_path": {"path": str(params.get("ls_path") or ".")},
            "read_file": {
                "path": file_path,
                "max_chars": int(params.get("read_max_chars") or 12000),
            },
            "grep_file": {
                "path": file_path,
                "pattern": str(params.get("grep_pattern") or r"第\s*\d+\s*章"),
                "regex": True,
                "ignore_case": True,
                "max_lines": int(params.get("grep_max_lines") or 120),
            },
        }
        return payload

    return payload


def _auto_tool_injection_enabled(mission: dict[str, Any], item: dict[str, Any]) -> bool:
    params = dict(item.get("params") or {})
    if "auto_tools" in params:
        return bool(params.get("auto_tools"))
    orch = orchestration_config(mission)
    return bool(orch.get("auto_tool_injection", False))


def apply_work_plan_to_payload(state: AgentState) -> AgentState:
    mission = state.get("mission") or {}
    if not orchestration_enabled(mission):
        return state

    from app.services.mission_intervention import (
        intervention_from_payload,
        intervention_to_writing_intent,
        is_forced,
    )

    from app.services.mission_execution import reconcile_work_plan

    state = reconcile_work_plan(state)
    state = ensure_next_work_item(state)
    payload = dict(state.get("input_payload") or {})
    intervention = intervention_from_payload(payload)
    if intervention and is_forced(intervention):
        step = int(state.get("mission_step") or 1)
        payload = dict(state.get("input_payload") or {})
        from app.services.mission_intervention import apply_intervention_to_payload

        payload = apply_intervention_to_payload(payload, intervention)
        wi = intervention.get("work_item")
        if wi or intervention.get("action") in ("edit_plot", "run_tools", "reset_body"):
            wi = wi or {
                "id": f"wi-forced-{step}",
                "kind": str(intervention.get("action") or "forced"),
                "title": str(intervention.get("action") or "forced"),
                "params": (
                    {"edit_spec": intervention.get("edit_spec") or {}}
                    if intervention.get("action") == "edit_plot"
                    else {}
                ),
            }
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
        item = get_current_work_item(state)
        if item:
            state = activate_work_item(state, item)
            payload = dict(state.get("input_payload") or payload)
            payload["current_work_item"] = item
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
    from app.config.settings import settings

    if _auto_tool_injection_enabled(mission, item):
        payload = _apply_tools_for_work_item(
            payload,
            item,
            default_body_name=str(getattr(settings, "MANUSCRIPT_DEFAULT_BODY", "novel.txt")),
        )
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


def format_completed_labels(completed: list[str], *, max_items: int = 8) -> str:
    """Compact completed list for Web CLI / SSE (avoids multi-kB single lines)."""
    labels = [str(x) for x in completed if str(x).strip()]
    if not labels:
        return "无"
    if len(labels) <= max_items:
        return "、".join(labels)
    head = "、".join(labels[:max_items])
    return f"{head}…（另有 {len(labels) - max_items} 项）"


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
    completed = format_completed_labels(list(detail["completed"] or []))
    current = detail.get("current_title")
    cur_status = detail.get("current_status")
    if current:
        status_note = f"（{cur_status}）" if cur_status and cur_status != "done" else ""
        current_part = f"当前：{current}{status_note}"
    else:
        current_part = "当前：无（待下一工作项）"
    return f"编排({mode}) 已完成 {done}/{total}：{completed}；{current_part}"


def orchestration_progress_brief(state: AgentState) -> str:
    """One-line progress for final_answer (no duplicate completed dump)."""
    detail = orchestration_detail(state)
    done = int(detail.get("done") or 0)
    total = int(detail.get("total") or 0)
    current = detail.get("current_title")
    if current:
        cur_status = detail.get("current_status")
        status_note = f"（{cur_status}）" if cur_status and cur_status != "done" else ""
        return f"编排进度 {done}/{total}，当前工作项：{current}{status_note}。"
    return f"编排进度 {done}/{total}。"
