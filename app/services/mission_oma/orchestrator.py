"""OMAW Orchestrator — mechanical dispatch, acceptance, unit work loop."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from app.config.settings import settings
from app.domain.mission import StepDecision
from app.domain.review_verdict import ReviewVerdict, load_review_verdict
from app.domain.worker_execution_policy import resolve_agent_capability
from app.runtime.state import AgentState, merge_state
from app.services.mission_oma.intent_spec import IntentSpec, intent_spec_from_payload
from app.services.mission_orchestrator import (
    get_current_work_item,
    orchestration_enabled,
    pending_work_items,
)
from app.services.turn_kind import resolve_turn_kind


def should_use_mission_oma(state: AgentState) -> bool:
    """True when writing mission should use OMAW mechanical orchestration."""
    mission = state.get("mission") or {}
    if str(mission.get("kind", "")).lower() != "writing":
        return False
    if not orchestration_enabled(mission):
        return False
    mode = str(
        state.get("execution_mode")
        or (state.get("input_payload") or {}).get("execution_mode")
        or ""
    ).lower()
    if mode == "mission_oma":
        return True
    return bool(getattr(settings, "MISSION_OMA_DEFAULT_FOR_WRITING", True))


def build_turn_envelope(
    state: AgentState,
    *,
    work_item: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Single-tick tactical envelope for audit/UI."""
    item = work_item or get_current_work_item(state) or {}
    kind = str(item.get("kind") or "")
    agent, capability = resolve_agent_capability(kind)
    turn_kind = resolve_turn_kind(state)
    payload = state.get("input_payload") or {}
    contract = payload.get("turn_contract") or {}
    return {
        "turn_kind": turn_kind,
        "contract": {"primary_op": str(contract.get("primary_op") or kind)},
        "work_item_id": str(item.get("id") or ""),
        "dispatch": {"to_agent": agent, "capability": capability},
    }


def _new_dispatch_id() -> str:
    return f"disp-{uuid.uuid4().hex[:12]}"


def _snapshot_version_for_batch(state: AgentState) -> str:
    ms = state.get("manuscript") or {}
    return f"o{ms.get('outline_bytes', 0)}-b{ms.get('body_bytes', 0)}"


def _work_item_from_head(state: AgentState) -> Optional[dict[str, Any]]:
    from app.services.mission_orchestrator import ensure_work_plan

    state = ensure_work_plan(state)
    return get_current_work_item(state)


def _plan_has_pending_polish(plan: dict[str, Any], chapter: int) -> bool:
    """True when work_plan already schedules polish for this chapter."""
    for row in plan.get("items") or []:
        if str(row.get("kind")) != "polish_chapter":
            continue
        if str(row.get("status")) not in ("pending", "in_progress"):
            continue
        try:
            ch = int((row.get("params") or {}).get("chapter_index") or 0)
        except (TypeError, ValueError):
            continue
        if ch == chapter:
            return True
    return False


def _phase_for_work_item(kind: str) -> str:
    mapping = {
        "append_body": "append_body",
        "append_chapter": "append_body",
        "write_body": "append_body",
        "write_outline": "write_outline",
        "review_chapter": "review_chapter",
        "polish_chapter": "polish_chapter",
        "chapter_summary": "chapter_summary",
        "consistency_check": "consistency_check",
        "reset_body": "reset_body",
    }
    return mapping.get(kind, kind)


def mechanical_step_decision(state: AgentState) -> StepDecision:
    """
    Mechanical dispatch from work_plan head + ReviewVerdict — no LLM phase pick.
    """
    from app.services.progress_evaluator import evaluate_mission_control

    eval_result = evaluate_mission_control(state)
    if eval_result.done and eval_result.action == "finish":
        return StepDecision(action="finish", rationale=eval_result.reason)
    if eval_result.done and eval_result.action == "pause":
        return StepDecision(action="pause", rationale=eval_result.reason)
    if eval_result.action == "escalate":
        return StepDecision(action="escalate", rationale=eval_result.reason)

    turn_kind = resolve_turn_kind(state)
    if turn_kind == "mechanical_continue":
        from app.services.writing_phases import resolve_chapter_index

        ch = resolve_chapter_index(state, {})
        return StepDecision(
            action="continue",
            next_executor="subgraph:writing",
            params={
                "writing_phase": "append_body",
                "chapter_index": ch,
                "oma_agent": "writer",
                "oma_capability": "write_chapter",
            },
            rationale="mechanical_continue grant → writer append",
        )
    if turn_kind == "steer_replan":
        return StepDecision(
            action="continue",
            next_executor="oma:planner",
            params={"writing_phase": "steer_replan", "oma_agent": "planner"},
            rationale="steer replan via planner worker",
        )

    from app.services.mission_orchestrator import work_plan_from_mission

    mission = state.get("mission") or {}
    plan = work_plan_from_mission(mission)
    if not (plan.get("items") or []):
        state = expand_unit_work_loop(state, unit_loop=str(mission.get("unit_loop") or "chapter_unit"))

    item = _work_item_from_head(state)
    if not item:
        from app.services.writing_phases import suggest_writing_phase_fallback

        return suggest_writing_phase_fallback(state)

    kind = str(item.get("kind") or "")
    params = dict(item.get("params") or {})
    chapter = params.get("chapter_index")
    agent, capability = resolve_agent_capability(kind)

    if params.get("conditional") == "verdict.polish_recommended" and chapter:
        try:
            ch = int(chapter)
        except (TypeError, ValueError):
            ch = 0
        if ch > 0:
            verdict = load_review_verdict(state["task_id"], ch)
            if not verdict or not verdict.polish_recommended:
                return StepDecision(
                    action="continue",
                    next_executor="subgraph:writing",
                    params={"skip_work_item": True},
                    rationale="polish not recommended — skip",
                )

    if params.get("after_polish") and chapter:
        try:
            ch = int(chapter)
        except (TypeError, ValueError):
            ch = 0
        if ch > 0:
            verdict = load_review_verdict(state["task_id"], ch)
            if verdict and verdict.qualified:
                return StepDecision(
                    action="continue",
                    next_executor="subgraph:writing",
                    params={"skip_work_item": True},
                    rationale="re-review skipped — already qualified",
                )

    # Inline polish only when work_plan has no explicit polish item (ADR §8.1 unit loop).
    progress = state.get("progress") or {}
    plan = progress.get("work_plan") or plan or {}
    if kind == "review_chapter" and chapter and not params.get("after_polish"):
        try:
            ch = int(chapter)
        except (TypeError, ValueError):
            ch = 0
        if ch > 0 and not _plan_has_pending_polish(plan, ch):
            verdict = load_review_verdict(state["task_id"], ch)
            if verdict and verdict.polish_recommended and not verdict.qualified:
                return StepDecision(
                    action="continue",
                    next_executor="subgraph:writing",
                    params={
                        "writing_phase": "polish_chapter",
                        "chapter_index": ch,
                        "oma_agent": "editor",
                        "oma_capability": "polish_chapter",
                    },
                    rationale="ReviewVerdict polish_recommended",
                )

    phase = _phase_for_work_item(kind)
    return StepDecision(
        action="continue",
        next_executor="subgraph:writing",
        params={
            "writing_phase": phase,
            "chapter_index": chapter,
            "oma_agent": agent,
            "oma_capability": capability,
            "fact_bundle_id": params.get("fact_bundle_id"),
            "work_item_id": item.get("id"),
        },
        rationale=f"oma mechanical dispatch → {agent}/{capability}",
    )


def expand_unit_work_loop(
    state: AgentState,
    *,
    unit_loop: str = "chapter_unit",
) -> AgentState:
    """
    Expand strategic unit loop into work_plan.items (non-LLM).
    retrieve facts → write → review → (polish) → gate → continue
    """
    from app.services.mission_orchestrator import append_work_items, ensure_work_plan

    if unit_loop != "chapter_unit":
        return state
    mission = state.get("mission") or {}
    progress = dict(state.get("progress") or {})
    plan = progress.get("work_plan") or {}
    if (plan.get("items") or []) and plan.get("mode") != "lazy":
        return state

    from app.services.manuscript_context import parse_last_chapter_index
    from app.services.manuscript_service import resolve_manuscript
    from app.services.manuscript_context import read_body_text

    task_id = state["task_id"]
    ms = resolve_manuscript(task_id, state.get("manuscript"))
    body = read_body_text(task_id, ms.body_path or "novel.txt", state=state)
    last_ch = parse_last_chapter_index(body)
    next_ch = max(1, last_ch + (1 if body.strip() else 0))

    items: list[dict[str, Any]] = []
    if int(ms.outline_bytes or 0) < 200:
        items.append(
            {
                "id": "wi-outline",
                "kind": "write_outline",
                "title": "Write outline",
                "status": "pending",
            }
        )
    items.append(
        {
            "id": f"wi-write-{next_ch}",
            "kind": "append_body",
            "title": f"Write chapter {next_ch}",
            "status": "pending",
            "params": {"chapter_index": next_ch},
        }
    )
    items.append(
        {
            "id": f"wi-review-{next_ch}",
            "kind": "review_chapter",
            "title": f"Review chapter {next_ch}",
            "status": "pending",
            "params": {"chapter_index": next_ch},
            "depends_on": [f"wi-write-{next_ch}"],
        }
    )
    items.append(
        {
            "id": f"wi-polish-{next_ch}",
            "kind": "polish_chapter",
            "title": f"Polish chapter {next_ch} (if needed)",
            "status": "pending",
            "params": {"chapter_index": next_ch, "conditional": "verdict.polish_recommended"},
            "depends_on": [f"wi-review-{next_ch}"],
        }
    )
    items.append(
        {
            "id": f"wi-rereview-{next_ch}",
            "kind": "review_chapter",
            "title": f"Re-review chapter {next_ch}",
            "status": "pending",
            "params": {"chapter_index": next_ch, "after_polish": True},
            "depends_on": [f"wi-polish-{next_ch}"],
        }
    )

    state = ensure_work_plan(state)
    progress = dict(state.get("progress") or {})
    plan = progress.get("work_plan") or {}
    plan = append_work_items(plan, items)
    plan["mode"] = "explicit"
    progress["work_plan"] = plan
    mission = {**mission, "unit_loop": unit_loop}
    return merge_state(state, progress=progress, mission=mission)


def check_acceptance(state: AgentState, spec: Optional[IntentSpec] = None) -> tuple[bool, str]:
    """Mechanical acceptance vs intent_spec.acceptance."""
    payload = state.get("input_payload") or {}
    intent = spec or intent_spec_from_payload(payload)
    acc = intent.acceptance
    chapters = intent.scope.chapters
    if not chapters:
        return True, "no_scope"

    task_id = state["task_id"]
    failures: list[str] = []
    for ch in chapters:
        verdict = load_review_verdict(task_id, ch)
        if acc.all_in_scope_reviewed and (not verdict or not verdict.is_valid()):
            failures.append(f"ch{ch}:not_reviewed")
            continue
        if verdict and not verdict.qualified:
            if acc.failed_must_polish_or_human:
                failures.append(f"ch{ch}:not_qualified")
    if failures:
        from app.services.metrics_service import get_metrics_service

        for f in failures:
            get_metrics_service().inc_acceptance_fail(f)
        return False, ";".join(failures)
    return True, "ok"


def stamp_dispatch_context(state: AgentState) -> AgentState:
    """Attach dispatch_id and turn_envelope before mission_act."""
    payload = dict(state.get("input_payload") or {})
    if not payload.get("dispatch_id"):
        payload["dispatch_id"] = _new_dispatch_id()
    item = get_current_work_item(state) or {}
    payload["turn_envelope"] = build_turn_envelope(state, work_item=item)
    return merge_state(state, input_payload=payload)


def parallel_review_subtasks(state: AgentState) -> Optional[list[dict[str, Any]]]:
    """Build A2A subtasks for parallel chapter review when scope has multiple chapters."""
    payload = state.get("input_payload") or {}
    intent = intent_spec_from_payload(payload)
    if intent.kind != "batch_review":
        return None
    chapters = intent.scope.chapters
    if len(chapters) < 2:
        return None

    from app.domain.agent_message import AgentMessage
    from app.services.manuscript_service import resolve_manuscript

    task_id = state["task_id"]
    ms = resolve_manuscript(task_id, state.get("manuscript"))
    manuscript_paths = {
        "body_path": ms.body_path,
        "outline_path": ms.outline_path,
        "task_id": task_id,
    }
    subtasks: list[dict[str, Any]] = []
    for ch in chapters:
        msg = AgentMessage.task(
            from_agent="orchestrator",
            to_agent="reviewer",
            capability="review_chapter",
            payload={
                "goal": f"Review chapter {ch}",
                "context": {
                    "manuscript_paths": manuscript_paths,
                    "chapter_index": ch,
                    "parent_task_id": task_id,
                },
            },
            correlation_id=task_id,
        )
        subtasks.append(
            {
                "subtask_id": msg.message_id,
                "domain": "writing",
                "required_capability": "review_chapter",
                "description": f"Review chapter {ch}",
                "status": "PENDING",
                "a2a_message": msg.to_dict(),
                "chapter_index": ch,
            }
        )
    return subtasks


def narrow_replan_after_acceptance_fail(state: AgentState) -> AgentState:
    """Planner narrow replan: only failed chapters from acceptance check."""
    from app.services.mission_orchestrator import append_work_items, ensure_work_plan

    payload = state.get("input_payload") or {}
    intent = intent_spec_from_payload(payload)
    ok, reason = check_acceptance(state, intent)
    if ok:
        return state

    failed: list[int] = []
    for part in str(reason).split(";"):
        if part.startswith("ch") and ":" in part:
            try:
                failed.append(int(part[1:].split(":")[0]))
            except ValueError:
                continue
    if not failed and intent.scope.chapters:
        from app.domain.review_verdict import load_review_verdict

        for ch in intent.scope.chapters:
            v = load_review_verdict(state["task_id"], ch)
            if not v or not v.qualified or not v.is_valid():
                failed.append(ch)

    if not failed:
        return state

    state = ensure_work_plan(state)
    progress = dict(state.get("progress") or {})
    plan = progress.get("work_plan") or {}
    new_items: list[dict[str, Any]] = []
    for ch in failed:
        new_items.append(
            {
                "id": f"wi-replan-review-{ch}",
                "kind": "review_chapter",
                "title": f"Re-review chapter {ch}",
                "status": "pending",
                "params": {"chapter_index": ch, "fact_bundle_id": None},
            }
        )
        new_items.append(
            {
                "id": f"wi-replan-polish-{ch}",
                "kind": "polish_chapter",
                "title": f"Polish chapter {ch}",
                "status": "pending",
                "params": {"chapter_index": ch},
                "depends_on": [f"wi-replan-review-{ch}"],
            }
        )
    plan = append_work_items(plan, new_items)
    progress["work_plan"] = plan
    payload = dict(payload)
    prev_results = payload.get("parallel_review_results")
    payload["acceptance_replan"] = {"failed_chapters": failed, "reason": reason}
    payload["steer_planning_done"] = True
    payload["require_planning_after_steer"] = False
    if prev_results:
        payload["parallel_review_results"] = prev_results
    return merge_state(
        state,
        progress=progress,
        input_payload=payload,
        audit_log=[
            *(state.get("audit_log") or []),
            {
                "node": "oma_orchestrator",
                "status": "narrow_replan",
                "detail": {"failed": failed, "reason": reason},
            },
        ],
    )


def run_parallel_reviews_if_applicable(state: AgentState) -> Optional[AgentState]:
    """Execute parallel reviewer workers via A2A (manuscript-bound, ADR 10.5)."""
    payload = state.get("input_payload") or {}
    intent = intent_spec_from_payload(payload)
    if intent.kind != "batch_review":
        return None
    chapters = intent.scope.chapters
    if len(chapters) < 2:
        return None

    subtasks = parallel_review_subtasks(state)
    if not subtasks:
        return None

    from app.services.a2a_dispatch import run_subtasks_via_a2a
    from app.services.manuscript_service import resolve_manuscript
    from app.services.metrics_service import get_metrics_service

    task_id = state["task_id"]
    ms = resolve_manuscript(task_id, state.get("manuscript"))
    snapshot = _snapshot_version_for_batch(state)
    context = {
        "manuscript_paths": {
            "body_path": ms.body_path,
            "outline_path": ms.outline_path,
            "task_id": task_id,
        },
        "parent_task_id": task_id,
        "fact_bundle_snapshot": snapshot,
    }
    max_workers = int(getattr(settings, "MISSION_REVIEW_MAX_PARALLEL", 3))
    metrics = get_metrics_service()
    for st in subtasks:
        metrics.inc_mission_dispatch("reviewer", "review_chapter")

    results_map, _updated, errors, tool_results = run_subtasks_via_a2a(
        parent_task_id=task_id,
        user_id=str(state.get("user_id") or "anonymous"),
        subtasks=subtasks,
        context=context,
        max_workers=max_workers,
    )
    results: dict[str, Any] = {}
    for st in subtasks:
        ch = st.get("chapter_index")
        sid = st.get("subtask_id")
        if sid in results_map:
            results[str(ch)] = results_map[sid]

    merged = merge_state(
        state,
        input_payload=dict(payload),
        tool_results=(state.get("tool_results") or []) + tool_results,
    )
    merged_payload = dict(merged.get("input_payload") or {})
    merged_payload["parallel_review_results"] = results
    if errors:
        merged_payload["parallel_review_errors"] = errors
    merged = merge_state(merged, input_payload=merged_payload)
    ok, reason = check_acceptance(merged, intent)
    merged_payload = dict(merged.get("input_payload") or {})
    merged_payload["acceptance_ok"] = ok
    merged_payload["acceptance_reason"] = reason
    merged = merge_state(merged, input_payload=merged_payload)
    if not ok:
        return narrow_replan_after_acceptance_fail(merged)
    return merged
