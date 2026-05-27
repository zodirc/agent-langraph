"""Unified ConfirmationBlock builder for intent / outcome gates."""

from __future__ import annotations

from typing import Any, Optional

from app.services.confirmation.preview_resolver import PreviewResult, resolve_outcome_preview
from app.services.mission_intervention import intervention_from_payload


def _queue_preview_items(state: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    plan = (state.get("progress") or {}).get("work_plan") or {}
    items = list(plan.get("items") or [])
    out: list[dict[str, Any]] = []
    for row in items:
        status = str(row.get("status") or "pending")
        if status in ("cancelled", "done"):
            continue
        out.append(
            {
                "id": row.get("id"),
                "kind": row.get("kind"),
                "title": row.get("title") or row.get("kind"),
                "status": status,
            }
        )
        if len(out) >= limit:
            break
    return out


def build_intent_confirmation_block(
    planning_result: dict[str, Any],
    payload: dict[str, Any],
    *,
    state: Optional[dict[str, Any]] = None,
    mission_before: Optional[dict[str, Any]] = None,
    task_id: Optional[str] = None,
) -> dict[str, Any]:
    """Human-facing structured block after planning interprets steer."""
    intervention = intervention_from_payload(payload) or {}
    raw = planning_result.get("mission_intervention")
    if isinstance(raw, dict) and raw.get("action"):
        intervention = raw

    plan = list(planning_result.get("plan") or [])
    llm_summary = str(planning_result.get("steer_intent_summary") or "").strip()

    lines: list[str] = []
    if llm_summary:
        lines.append(llm_summary)
    else:
        lines.append("规划模型已根据你的介入更新执行方案，请确认后再继续。")

    if intervention.get("action"):
        lines.append(f"拟执行动作：{intervention['action']}")
        if intervention.get("force"):
            lines.append("（强制覆盖 step_policy）")

    mission = payload.get("mission") if isinstance(payload.get("mission"), dict) else {}
    total = mission.get("total_target_chars") or (mission.get("success_criteria") or {}).get("target")
    if total:
        before = mission_before or {}
        prev = before.get("total_target_chars") or (before.get("success_criteria") or {}).get("target")
        if prev and prev != total:
            lines.append(f"全书目标字数：{prev} → {total}")
        else:
            lines.append(f"全书目标字数：{total}")

    sp = mission.get("step_policy") or {}
    if sp.get("chars_per_step"):
        lines.append(f"每步字数：{sp.get('chars_per_step')}")

    intent = payload.get("writing_intent") or {}
    if isinstance(intent, dict) and intent.get("action"):
        ch = intent.get("chapter_index")
        ch_part = f"，chapter_index={ch}" if ch is not None else ""
        lines.append(f"写作意图：{intent.get('action')}（enabled={intent.get('enabled')}{ch_part}）")

    sections: list[dict[str, Any]] = [
        {"type": "text", "role": "summary", "content": "\n".join(lines)},
    ]

    if intervention:
        sections.append(
            {
                "type": "intervention",
                "action": intervention.get("action"),
                "force": bool(intervention.get("force")),
                "reason": str(intervention.get("reason") or "").strip() or None,
            }
        )

    impact = payload.get("steer_replan_impact")
    if isinstance(impact, dict) and impact.get("operations"):
        sections.append({"type": "impact", **impact})

    if state:
        queue_items = _queue_preview_items(state)
        if queue_items:
            sections.append({"type": "queue", "items": queue_items})
        elif plan:
            sections.append(
                {
                    "type": "queue",
                    "items": [{"title": str(s), "kind": "plan_step", "status": "planned"} for s in plan[:6]],
                }
            )

    steer_msgs = [
        str(m.get("content") or "")
        for m in (payload.get("conversation_history") or [])
        if m.get("steer")
    ][-3:]

    block: dict[str, Any] = {
        "summary_text": "\n".join(lines),
        "plan": plan,
        "mission_intervention": intervention or None,
        "steer_messages": steer_msgs,
        "requires_confirm": True,
        "phase": "intent",
        "sections": sections,
    }
    if task_id:
        from app.services.steer_confirmation_actions import enrich_confirmation_block

        block = enrich_confirmation_block(task_id, block)
    return block


def build_outcome_confirmation_block(
    state: dict[str, Any],
    completed_item: dict[str, Any],
) -> dict[str, Any]:
    mission = state.get("mission") or {}
    payload = state.get("input_payload") or {}
    kind = str(completed_item.get("kind") or "work_item")
    title = str(completed_item.get("title") or kind)
    preview: PreviewResult = resolve_outcome_preview(state, completed_item)

    intervention = intervention_from_payload(payload) or {}
    action = intervention.get("action")

    lines = [
        f"已完成工作项「{title}」，以下是 {preview.filename} 的预览，请确认是否符合你的介入意图：",
    ]
    if action:
        lines.append(f"关联介入动作：{action}")
    if preview.mode == "delta":
        lines.append("（本步新增内容节选）")
    elif preview.mode == "diff":
        lines.append("（变更 diff 节选）")
    elif preview.truncated:
        lines.append(f"（节选，全文共 {preview.total_chars} 字符）")

    sections: list[dict[str, Any]] = [
        {"type": "text", "role": "summary", "content": "\n".join(lines)},
    ]
    if preview.content.strip():
        sections.append(preview.to_section())

    queue_items = _queue_preview_items(state, limit=2)
    if queue_items:
        sections.append({"type": "queue", "role": "next", "items": queue_items})

    task_id = state["task_id"]
    from app.services.steer_confirmation_actions import build_confirmation_actions

    actions = build_confirmation_actions(task_id)
    lines.append("若要修改，请发送新的 steer（勿带 confirm:true）。")

    block: dict[str, Any] = {
        "summary_text": "\n".join(lines),
        "work_item_id": completed_item.get("id"),
        "work_item_kind": kind,
        "artifact_filename": preview.filename,
        "artifact_excerpt": preview.content[:4500],
        "preview_mode": preview.mode,
        "preview_truncated": preview.truncated,
        "preview_total_chars": preview.total_chars,
        "requires_confirm": True,
        "phase": "outcome",
        "user_actions": actions,
        "sections": sections,
    }
    from app.services.steer_confirmation_actions import enrich_confirmation_block

    return enrich_confirmation_block(task_id, block)
