"""
Steer intent confirmation — after planning interprets user steer, pause for explicit OK
before mission_act continues (confirm-before-execute for material changes).
"""

from __future__ import annotations

from typing import Any, Optional

from app.runtime.state import AgentState, TaskStatus, merge_state
from app.services.mission_intervention import intervention_from_payload

_MATERIAL_INTERVENTION_ACTIONS = frozenset(
    {
        "rewrite_outline",
        "reset_body",
        "edit_plot",
        "review_outline",
        "enqueue_work",
    }
)


def steer_confirmation_pending(payload: dict[str, Any]) -> bool:
    return payload.get("steer_intent_pending_confirm") is True


def steer_confirmation_required(
    planning_result: dict[str, Any],
    payload: dict[str, Any],
    *,
    mission_before: Optional[dict[str, Any]] = None,
) -> bool:
    """True when steer planning produced a material change worth user OK before act."""
    if payload.get("steer_intent_confirmed"):
        return False
    if not payload.get("steer_applied_at"):
        return False

    intervention = intervention_from_payload(payload) or {}
    raw = planning_result.get("mission_intervention")
    if isinstance(raw, dict) and raw.get("action"):
        intervention = raw

    action = str((intervention or {}).get("action") or "")
    if action in _MATERIAL_INTERVENTION_ACTIONS:
        return True
    if intervention and intervention.get("force"):
        return True

    mission_after = payload.get("mission") if isinstance(payload.get("mission"), dict) else {}
    before = mission_before or {}
    try:
        after_total = int(
            mission_after.get("total_target_chars")
            or (mission_after.get("success_criteria") or {}).get("target")
            or 0
        )
        before_total = int(
            before.get("total_target_chars")
            or (before.get("success_criteria") or {}).get("target")
            or 0
        )
    except (TypeError, ValueError):
        after_total = before_total = 0
    if after_total > 0 and after_total != before_total:
        return True

    if planning_result.get("steer_intent_summary"):
        return True

    return False


def build_steer_intent_summary(
    planning_result: dict[str, Any],
    payload: dict[str, Any],
    *,
    mission_before: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Human-facing summary of how planning interpreted the latest steer."""
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
    total = mission.get("total_target_chars") or (mission.get("success_criteria") or {}).get(
        "target"
    )
    if total:
        before = mission_before or {}
        prev = before.get("total_target_chars") or (before.get("success_criteria") or {}).get(
            "target"
        )
        if prev and prev != total:
            lines.append(f"全书目标字数：{prev} → {total}")
        else:
            lines.append(f"全书目标字数：{total}")

    sp = mission.get("step_policy") or {}
    if sp.get("chars_per_step"):
        lines.append(f"每步字数：{sp.get('chars_per_step')}")

    if plan:
        lines.append("计划步骤：" + "；".join(str(s) for s in plan[:6]))

    intent = payload.get("writing_intent") or {}
    if isinstance(intent, dict) and intent.get("action"):
        lines.append(f"写作意图：{intent.get('action')}（enabled={intent.get('enabled')}）")

    steer_msgs = [
        str(m.get("content") or "")
        for m in (payload.get("conversation_history") or [])
        if m.get("steer")
    ][-3:]

    return {
        "summary_text": "\n".join(lines),
        "plan": plan,
        "mission_intervention": intervention or None,
        "steer_messages": steer_msgs,
        "requires_confirm": True,
        "phase": "intent",
    }


def apply_steer_confirmation_pending(
    payload: dict[str, Any],
    confirmation: dict[str, Any],
    *,
    task_id: Optional[str] = None,
) -> dict[str, Any]:
    tid = task_id or str(payload.get("task_id") or "")
    if tid and "user_actions" not in confirmation:
        from app.services.steer_confirmation_actions import enrich_confirmation_block

        confirmation = enrich_confirmation_block(tid, confirmation)
    out = dict(payload)
    if tid:
        out["task_id"] = tid
    out["steer_intent_pending_confirm"] = True
    out["steer_intent_confirmed"] = False
    out["steer_intent_confirmation"] = confirmation
    out.pop("steer_intent_confirmed_at", None)
    return out


def clear_steer_confirmation_flags(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    out["steer_intent_pending_confirm"] = False
    out.pop("steer_intent_confirmation", None)
    return out


def confirm_steer_intent(state: AgentState) -> AgentState:
    """User approved planning interpretation — allow mission_act to execute."""
    payload = clear_steer_confirmation_flags(dict(state.get("input_payload") or {}))
    payload["steer_intent_confirmed"] = True
    from datetime import datetime, timezone

    payload["steer_intent_confirmed_at"] = datetime.now(timezone.utc).isoformat()
    return merge_state(
        state,
        input_payload=payload,
        status=TaskStatus.MISSION_RUNNING.value,
        mission_control=None,
    )


def attach_steer_confirmation_to_state(state: AgentState) -> AgentState:
    """Set reasoning summary for pause turn so Web/API can show confirmation block."""
    payload = state.get("input_payload") or {}
    block = payload.get("steer_intent_confirmation") or {}
    text = str(block.get("summary_text") or "请确认本次介入理解后再继续执行。")
    reasoning_result = {
        "summary": text,
        "confidence": 0.9,
        "risk_level": "LOW",
        "structured": {"source": "steer_intent_confirmation", "confirmation": block},
    }
    return merge_state(
        state,
        reasoning_result=reasoning_result,
        final_answer=text,
        status=TaskStatus.REASONED.value,
    )
