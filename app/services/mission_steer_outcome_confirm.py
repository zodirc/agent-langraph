"""
Steer outcome confirmation — after a material work item finishes, pause with an artifact
excerpt so the user can verify results match their steer (confirm-after-execute).
"""

from __future__ import annotations

from typing import Any, Optional

from app.domain.mission import StepPolicy
from app.runtime.state import AgentState, TaskStatus, merge_state
from app.services.mission_intervention import intervention_from_payload

# Work-item kinds that warrant a post-execution acceptance gate after steer.
_OUTCOME_WORK_ITEM_KINDS = frozenset(
    {
        "write_outline",
        "edit_plot",
    }
)

_INTERVENTION_WATCH_ACTIONS = frozenset(
    {
        "rewrite_outline",
        "reset_body",
        "edit_plot",
    }
)

_EXCERPT_MAX_CHARS = 4500


def steer_outcome_confirmation_pending(payload: dict[str, Any]) -> bool:
    return payload.get("steer_outcome_pending_confirm") is True


def _steer_watch_outcome(payload: dict[str, Any]) -> bool:
    if payload.get("steer_watch_outcome"):
        return True
    if payload.get("require_planning_after_steer"):
        return True
    if payload.get("steer_applied_at"):
        return True
    intervention = intervention_from_payload(payload) or {}
    return str(intervention.get("action") or "") in _INTERVENTION_WATCH_ACTIONS


def _outcome_already_confirmed(payload: dict[str, Any], work_item_id: str) -> bool:
    confirmed = payload.get("steer_outcome_confirmed_for")
    if confirmed and str(confirmed) == str(work_item_id):
        return True
    batch = payload.get("steer_applied_at")
    batches = payload.get("steer_outcome_confirmed_batches") or []
    if batch and batch in batches and not work_item_id:
        return True
    return False


def _artifact_filename_for_item(
    state: AgentState,
    item: dict[str, Any],
    *,
    mission: dict[str, Any],
) -> str:
    kind = str(item.get("kind") or "")
    manuscript = state.get("manuscript") or {}
    policy = StepPolicy.from_dict(mission.get("step_policy") or {})
    if kind == "write_outline":
        return str(manuscript.get("outline_path") or policy.outline_artifact or "outline.txt")
    if kind == "edit_plot":
        payload = state.get("input_payload") or {}
        spec = payload.get("edit_plot_spec") or (item.get("params") or {}).get("edit_spec") or {}
        return str(spec.get("filename") or manuscript.get("body_path") or "novel.txt")
    return str(manuscript.get("body_path") or "novel.txt")


def _read_excerpt(state: AgentState, filename: str) -> str:
    from app.services.artifact_content import _read_artifact_snippet

    task_id = state["task_id"]
    return _read_artifact_snippet(
        task_id,
        filename,
        max_chars=_EXCERPT_MAX_CHARS,
        state=state,
    ).strip()


def steer_outcome_confirmation_required(
    state: AgentState,
    completed_item: dict[str, Any],
) -> bool:
    payload = state.get("input_payload") or {}
    if steer_outcome_confirmation_pending(payload):
        return False
    from app.services.mission_steer_confirm import steer_confirmation_pending

    if steer_confirmation_pending(payload):
        return False

    observation = state.get("observation") or {}
    if observation.get("has_failures"):
        return False

    if not _steer_watch_outcome(payload):
        return False

    kind = str(completed_item.get("kind") or "")
    if kind not in _OUTCOME_WORK_ITEM_KINDS:
        return False

    work_item_id = str(completed_item.get("id") or f"wi-{state.get('mission_step')}")
    if _outcome_already_confirmed(payload, work_item_id):
        return False

    mission = state.get("mission") or {}
    filename = _artifact_filename_for_item(state, completed_item, mission=mission)
    if not _read_excerpt(state, filename):
        return False

    return True


def build_steer_outcome_summary(
    state: AgentState,
    completed_item: dict[str, Any],
) -> dict[str, Any]:
    mission = state.get("mission") or {}
    payload = state.get("input_payload") or {}
    kind = str(completed_item.get("kind") or "work_item")
    title = str(completed_item.get("title") or kind)
    filename = _artifact_filename_for_item(state, completed_item, mission=mission)
    excerpt = _read_excerpt(state, filename)

    intervention = intervention_from_payload(payload) or {}
    action = intervention.get("action")

    lines = [
        f"已完成工作项「{title}」，以下是 {filename} 的节选，请确认是否符合你的介入意图：",
    ]
    if action:
        lines.append(f"关联介入动作：{action}")
    task_id = state["task_id"]
    from app.services.steer_confirmation_actions import build_confirmation_actions

    actions = build_confirmation_actions(task_id)
    lines.append("若要修改，请发送新的 steer（勿带 confirm:true）。")

    block: dict[str, Any] = {
        "summary_text": "\n".join(lines),
        "work_item_id": completed_item.get("id"),
        "work_item_kind": kind,
        "artifact_filename": filename,
        "artifact_excerpt": excerpt[:4500],
        "requires_confirm": True,
        "phase": "outcome",
        "user_actions": actions,
    }
    return block


def apply_steer_outcome_confirmation_pending(
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
    out["steer_outcome_pending_confirm"] = True
    out["steer_outcome_confirmed"] = False
    out["steer_outcome_confirmation"] = confirmation
    out.pop("steer_outcome_confirmed_at", None)
    return out


def clear_steer_outcome_flags(payload: dict[str, Any]) -> dict[str, Any]:
    """Explicit False so state_store volatile merge does not resurrect stale gates."""
    out = dict(payload)
    out["steer_outcome_pending_confirm"] = False
    out.pop("steer_outcome_confirmation", None)
    out.pop("steer_outcome_confirmed", None)
    return out


def confirm_steer_outcome(state: AgentState) -> AgentState:
    """User accepted the executed work item — allow mission loop to continue."""
    payload = dict(state.get("input_payload") or {})
    block = payload.get("steer_outcome_confirmation") or {}
    work_item_id = block.get("work_item_id")
    if work_item_id:
        payload["steer_outcome_confirmed_for"] = str(work_item_id)
    batch = payload.get("steer_applied_at")
    batches = list(payload.get("steer_outcome_confirmed_batches") or [])
    if batch and batch not in batches:
        batches.append(batch)
    payload["steer_outcome_confirmed_batches"] = batches
    payload = clear_steer_outcome_flags(payload)
    payload["steer_outcome_confirmed"] = True
    from datetime import datetime, timezone

    payload["steer_outcome_confirmed_at"] = datetime.now(timezone.utc).isoformat()
    payload.pop("steer_watch_outcome", None)
    return merge_state(
        state,
        input_payload=payload,
        status=TaskStatus.MISSION_RUNNING.value,
        mission_control=None,
    )


def apply_outcome_confirmation_after_work_item(
    state: AgentState,
    completed_item: dict[str, Any],
) -> AgentState:
    if not steer_outcome_confirmation_required(state, completed_item):
        return state
    payload = state.get("input_payload") or {}
    summary = build_steer_outcome_summary(state, completed_item)
    payload = apply_steer_outcome_confirmation_pending(
        payload, summary, task_id=state["task_id"]
    )
    state = merge_state(state, input_payload=payload)
    return attach_steer_outcome_confirmation_to_state(state)


def attach_steer_outcome_confirmation_to_state(state: AgentState) -> AgentState:
    payload = state.get("input_payload") or {}
    block = payload.get("steer_outcome_confirmation") or {}
    text = str(block.get("summary_text") or "请确认本次执行结果后再继续。")
    reasoning_result = {
        "summary": text,
        "confidence": 0.9,
        "risk_level": "LOW",
        "structured": {"source": "steer_outcome_confirmation", "confirmation": block},
    }
    return merge_state(
        state,
        reasoning_result=reasoning_result,
        final_answer=text,
        status=TaskStatus.REASONED.value,
    )
