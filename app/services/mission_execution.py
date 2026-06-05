"""
Mission execution control — execution grants, pause reasons, work-plan reconcile.

Mechanical layer (no NLP): manuscript + step_policy drive transitions; work_plan is a
projection that must stay consistent with artifact facts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.config.settings import settings
from app.domain.mission import StepPolicy
from app.runtime.state import AgentState, merge_state
from app.services.manuscript_service import manuscript_has_body, resolve_manuscript

# Pause reasons (stored on mission_control.pause_reason)
PAUSE_STEP_CHECKPOINT = "step_checkpoint"
PAUSE_GATE_INTENT = "gate_intent"
PAUSE_GATE_OUTCOME = "gate_outcome"
PAUSE_STEER_QUEUED = "steer_queued"
PAUSE_HUMAN_GATE = "human_gate"
PAUSE_FAILURE = "failure"
PAUSE_BUDGET = "budget"
PAUSE_FORCED = "forced"
PAUSE_WORKER_LOST = "worker_lost"
PAUSE_USER_REQUESTED_PAUSE = "user_requested_pause"
PAUSE_USER_REQUESTED_CANCEL = "user_requested_cancel"

_MECHANICAL_RESUME_SOURCES = frozenset(
    {
        "continue_signal",
        "explicit_request",
        "resume_api",
        "intervention",
    }
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_mechanical_resume_decision(decision: Any) -> bool:
    """Resume without mandatory planning (control-plane / continue signal).

    Semantic mixed input (steer + continue) must pass intent observation first;
    see mechanical_resume_allowed() and intent_observation_policy.
    """
    source = str(getattr(decision, "source", "") or (decision or {}).get("source", ""))
    intent = str(getattr(decision, "intent", "") or (decision or {}).get("intent", ""))
    if intent != "resume_mission":
        return False
    if source in _MECHANICAL_RESUME_SOURCES:
        return True
    if source.startswith("pattern_kind"):
        return True
    return False


def mechanical_resume_allowed(state: AgentState, decision: Any) -> bool:
    """Combine mechanical decision with intent observation guard."""
    if not is_mechanical_resume_decision(decision):
        return False
    from app.services.intent_observation_policy import mechanical_resume_allowed_by_observation

    return mechanical_resume_allowed_by_observation(state)


def issue_execution_grant_to_payload(
    payload: dict[str, Any],
    *,
    source: str = "resume",
    scope: Optional[str] = None,
) -> dict[str, Any]:
    """Grant one mission_act; clear steer gates only when mechanical resume is safe."""
    from app.services.intent_composer import (
        GRANT_SCOPE_MECHANICAL_RESUME,
        may_issue_execution_grant,
        record_grant_steer_conflict,
        resolve_grant_scope,
    )
    from app.services.turn_contract_lifecycle import (
        REASON_EXECUTION_GRANT,
        invalidate_turn_contract_payload,
    )

    if not may_issue_execution_grant(payload):
        return record_grant_steer_conflict(payload, reason="steer_planning_pending")

    resolved_scope = str(scope or resolve_grant_scope(source=source))
    out = invalidate_turn_contract_payload(dict(payload), REASON_EXECUTION_GRANT)
    out["execution_grant"] = {
        "issued_at": _now_iso(),
        "source": str(source),
        "scope": resolved_scope,
        "consume_once": True,
    }
    if resolved_scope == GRANT_SCOPE_MECHANICAL_RESUME:
        out["steer_planning_done"] = True
        out.pop("require_planning_after_steer", None)
    intervention = out.get("mission_intervention")
    if isinstance(intervention, dict):
        if str(intervention.get("action") or "") == "pause" and bool(intervention.get("force")):
            out.pop("mission_intervention", None)
    for key in (
        "steer_intent_pending_confirm",
        "steer_intent_confirmation",
        "steer_intent_confirmed",
        "steer_outcome_pending_confirm",
        "steer_outcome_confirmation",
        "steer_outcome_confirmed",
        "steer_outcome_confirmed_for",
    ):
        out.pop(key, None)
    return out


def issue_execution_grant(state: AgentState, *, source: str = "resume") -> AgentState:
    payload = issue_execution_grant_to_payload(state.get("input_payload") or {}, source=source)
    return merge_state(state, input_payload=payload)


def has_execution_grant(payload: dict[str, Any] | None = None, *, state: AgentState | None = None) -> bool:
    data = payload if payload is not None else (state.get("input_payload") if state else {}) or {}
    grant = data.get("execution_grant")
    return isinstance(grant, dict) and bool(grant.get("consume_once", True))


def consume_execution_grant(state: AgentState) -> AgentState:
    """Remove execution_grant (merge_state cannot drop nested keys via partial update)."""
    from app.runtime.state import ensure_agent_state

    payload = dict(state.get("input_payload") or {})
    grant = payload.pop("execution_grant", None)
    if grant:
        payload["last_execution_grant"] = grant
    merged = dict(state)
    merged["input_payload"] = payload
    return ensure_agent_state(merged)


def work_item_satisfied(
    kind: str,
    *,
    state: AgentState,
    mission: dict[str, Any],
    params: Optional[dict[str, Any]] = None,
) -> bool:
    """Whether a work-plan item kind is already satisfied by manuscript facts."""
    stored = state.get("manuscript") or {}
    ms = resolve_manuscript(state["task_id"], stored)
    outline_bytes = max(int(ms.outline_bytes or 0), int(stored.get("outline_bytes") or 0))
    body_bytes = max(int(ms.body_bytes or 0), int(stored.get("body_bytes") or 0))
    outline_path = ms.outline_path or stored.get("outline_path")
    body_path = ms.body_path or stored.get("body_path")
    min_outline = int(getattr(settings, "MANUSCRIPT_MIN_OUTLINE_CHARS", 80))
    min_body = int(getattr(settings, "MANUSCRIPT_MIN_BODY_CHARS", 200))

    if kind == "write_outline":
        return bool(outline_path) and outline_bytes >= min_outline
    if kind in ("write_body", "append_body", "append_chapter"):
        return bool(body_path) and body_bytes >= min_body
    if kind == "reset_body":
        return not manuscript_has_body(ms) or int(ms.body_bytes or 0) == 0
    if kind in ("review_chapter", "polish_chapter"):
        from app.domain.review_verdict import ReviewVerdict, load_review_verdict
        from app.services.writing_phases import _chapter_key, load_chapter_reviews

        wi_params = ((state.get("input_payload") or {}).get("current_work_item") or {}).get(
            "params"
        )
        row_params = params if isinstance(params, dict) else (wi_params if isinstance(wi_params, dict) else {})
        chapter = int(row_params.get("chapter_index") or 0)
        if chapter < 1:
            return False
        verdict = load_review_verdict(state["task_id"], chapter)
        if verdict and verdict.is_valid():
            rev = verdict.to_dict()
        else:
            reviews = load_chapter_reviews(state["task_id"])
            rev = (reviews.get("reviews") or {}).get(_chapter_key(chapter)) or {}
            verdict = ReviewVerdict.from_dict(rev)
    if kind == "review_chapter":
        if bool(row_params.get("after_polish")):
            return bool(verdict and verdict.qualified)
        if verdict and verdict.is_valid():
            return True
        return bool(rev)
    if kind == "polish_chapter":
        if verdict and verdict.qualified and not verdict.polish_recommended:
            return True
        if rev and rev.get("pass") and not rev.get("polish_recommended", True):
            return True
        return bool(rev.get("polished_at") or rev.get("polish_skipped"))
    return False


def compute_artifact_delta(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> dict[str, Any]:
    """Byte deltas for outline/body artifacts between two manuscript snapshots."""
    before = before or {}
    after = after or {}
    delta: dict[str, Any] = {}
    for key, path_key in (
        ("outline", "outline_path"),
        ("body", "body_path"),
    ):
        path = after.get(path_key) or before.get(path_key)
        prev_b = int(before.get(f"{key}_bytes") or 0)
        next_b = int(after.get(f"{key}_bytes") or 0)
        diff = next_b - prev_b
        if diff != 0 or (path and next_b > 0 and prev_b == 0):
            delta[key] = {
                "path": path,
                "before_bytes": prev_b,
                "after_bytes": next_b,
                "delta_bytes": diff,
            }
    delta["has_change"] = bool(delta)
    return delta


def append_transition_record(state: AgentState, record: dict[str, Any]) -> AgentState:
    progress = dict(state.get("progress") or {})
    history = list(progress.get("transition_log") or [])
    history.append({**record, "at": _now_iso()})
    progress["transition_log"] = history[-100:]
    return merge_state(state, progress=progress)


def reconcile_work_plan(state: AgentState) -> AgentState:
    """
    Align lazy/explicit work_plan with manuscript + step_policy (idempotent).

    - Mark pending/running items done when satisfied by artifact predicates
    - Drop duplicate pending items of the same kind when already satisfied
    - Propagate blocked dependents when an item is failed
    - Enqueue next lazy item when no runnable work remains
    """
    from app.services.mission_orchestrator import (
        _has_pending_items,
        _plan,
        append_work_items,
        build_next_lazy_work_item,
        orchestration_enabled,
    )
    from app.services.task_agenda import agenda_summary, ensure_agenda_fields, propagate_failure

    mission = state.get("mission") or {}
    if not orchestration_enabled(mission):
        return state

    plan = ensure_agenda_fields(dict(_plan(state)))
    items = list(plan.get("items") or [])
    if not items and plan.get("mode") != "lazy":
        return state

    changed = False
    for idx, row in enumerate(items):
        kind = str(row.get("kind") or "")
        status = str(row.get("status") or "pending")
        if status in ("done", "cancelled", "superseded"):
            continue
        if status == "failed":
            plan = propagate_failure(plan, str(row.get("id") or ""))
            items = list(plan.get("items") or [])
            changed = True
            continue
        if work_item_satisfied(
            kind,
            state=state,
            mission=mission,
            params=row.get("params") if isinstance(row.get("params"), dict) else None,
        ):
            items[idx] = {
                **row,
                "status": "done",
                "superseded_by": "manuscript_reconcile",
            }
            changed = True

    def _pending_dedupe_key(row: dict[str, Any]) -> str:
        kind = str(row.get("kind") or "")
        params = row.get("params") if isinstance(row.get("params"), dict) else {}
        if kind == "review_chapter":
            return (
                f"{kind}:{params.get('chapter_index')}:"
                f"{bool(params.get('after_polish'))}"
            )
        if kind in ("polish_chapter", "append_body", "write_body", "append_chapter"):
            return f"{kind}:{params.get('chapter_index')}"
        return kind

    seen_pending_keys: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in items:
        status = str(row.get("status") or "pending")
        dedupe_key = _pending_dedupe_key(row)
        if status == "pending" and dedupe_key in seen_pending_keys:
            deduped.append(
                {**row, "status": "superseded", "superseded_by": "duplicate_pending"}
            )
            changed = True
            continue
        if status == "pending":
            seen_pending_keys.add(dedupe_key)
        deduped.append(row)
    items = deduped

    plan["items"] = items
    plan["total_items"] = len(items)
    progress = dict(state.get("progress") or {})
    progress["work_plan"] = plan
    progress["agenda_summary"] = agenda_summary(plan)
    state = merge_state(state, progress=progress)

    plan = ensure_agenda_fields(dict(_plan(state)))
    if plan.get("mode") == "lazy" and not _has_pending_items(plan):
        item = build_next_lazy_work_item(state, mission)
        if item:
            progress = dict(state.get("progress") or {})
            progress["work_plan"] = append_work_items(plan, [item])
            progress["agenda_summary"] = agenda_summary(progress["work_plan"])
            state = merge_state(state, progress=progress)
            changed = True

    if changed:
        state = append_transition_record(
            state,
            {
                "event": "work_plan_reconciled",
                "mission_step": state.get("mission_step"),
            },
        )
    return state


def build_mission_checkpoint_summary(state: AgentState) -> dict[str, Any]:
    """Mechanical user-facing summary when no artifact was written this turn."""
    mission = state.get("mission") or {}
    progress = state.get("progress") or {}
    metrics = progress.get("metrics") or {}
    control = state.get("mission_control") or {}
    pause_reason = control.get("pause_reason") or PAUSE_STEP_CHECKPOINT

    from app.services.mission_orchestrator import orchestration_detail, orchestration_enabled

    lines: list[str] = []
    written = metrics.get("written_chars", 0)
    target = metrics.get("target_chars") or (mission.get("success_criteria") or {}).get(
        "target"
    )
    if target:
        lines.append(f"写作进度：{written}/{int(target)} 字（{metrics.get('progress_pct', 0)}%）。")
    manuscript = state.get("manuscript") or {}
    if manuscript.get("outline_bytes"):
        lines.append(f"大纲：{manuscript.get('outline_path')}（{manuscript['outline_bytes']} 字节）。")
    if manuscript.get("body_bytes"):
        lines.append(f"正文：{manuscript.get('body_path')}（{manuscript['body_bytes']} 字节）。")
    elif int(manuscript.get("outline_bytes") or 0) > 0:
        lines.append("正文尚未开始，接下来会按章节计划写入第一章。")

    if orchestration_enabled(mission):
        detail = orchestration_detail(state)
        cur = detail.get("current_title")
        if cur:
            lines.append(f"当前工作项：{cur}（{detail.get('current_status')}）。")

    if pause_reason == PAUSE_STEP_CHECKPOINT:
        lines.append(
            "本步已写完并暂停。直接发送「继续」或「下一步」，"
            "或在界面使用「继续任务」即可接着写正文，无需重复说明任务内容。"
        )
    elif pause_reason in (PAUSE_GATE_INTENT, PAUSE_GATE_OUTCOME):
        lines.append("请先确认上一步的理解或结果，确认后会自动继续写作。")

    summary = " ".join(lines) if lines else "任务已暂停，等待继续执行。"
    return {
        "summary": summary,
        "confidence": 0.95,
        "risk_level": "LOW",
        "structured": {
            "source": "mission_checkpoint",
            "pause_reason": pause_reason,
            "orchestration": orchestration_detail(state) if orchestration_enabled(mission) else None,
        },
    }


def should_skip_llm_reasoning_on_finalize(state: AgentState) -> bool:
    """
    Writing missions without artifact delta this turn should not run full reasoning
    (avoids regurgitating outline into the user answer).
    """
    from app.services.turn_contract import (
        contract_requires_side_effects,
        is_turn_contract_fulfilled,
    )

    mission = state.get("mission") or {}
    if str(mission.get("kind")) != "writing":
        return False
    payload = state.get("input_payload") or {}
    if contract_requires_side_effects(payload, state=state) and not is_turn_contract_fulfilled(
        state
    ):
        return True
    obs = state.get("observation") or {}
    delta = obs.get("artifact_delta") or {}
    if delta.get("has_change"):
        return False
    if state.get("reasoning_result"):
        rr = state.get("reasoning_result") or {}
        if (rr.get("structured") or {}).get("source") == "mission_writing_skip":
            return True
        return False
    control = state.get("mission_control") or {}
    if control.get("action") == "pause":
        return True
    return False
