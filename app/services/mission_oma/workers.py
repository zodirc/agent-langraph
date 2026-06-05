"""OMAW Worker adapters — writer/reviewer/editor/planner/continuity."""

from __future__ import annotations

from typing import Any, Optional

from app.domain.worker_execution_policy import policy_for_work_item
from app.runtime.state import AgentState, merge_state
from app.services.fact_bundle_builder import attach_fact_bundle_to_state, build_fact_bundle
from app.services.metrics_service import get_metrics_service
from app.services.mission_oma.chapter_lock import chapter_artifact_lock


def _intent_action(payload: dict[str, Any]) -> str:
    wi = payload.get("writing_intent") or {}
    return str(wi.get("action") or wi.get("writing_phase") or "")


def _chapter_from_state(state: AgentState) -> Optional[int]:
    payload = state.get("input_payload") or {}
    intent = payload.get("writing_intent") or {}
    decision = state.get("step_decision") or {}
    params = dict(decision.get("params") or {})
    raw = (
        intent.get("chapter_index")
        or params.get("chapter_index")
        or (payload.get("current_work_item") or {}).get("params", {}).get("chapter_index")
    )
    if raw is None:
        return None
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return None


def prepare_worker_execution(state: AgentState) -> AgentState:
    """
    Pre-worker: build FactBundle, stamp policy, require bundle when configured.
    """
    from app.config.settings import settings

    payload = state.get("input_payload") or {}
    decision = state.get("step_decision") or {}
    params = dict(decision.get("params") or {})
    item = payload.get("current_work_item") or {}
    kind = str(item.get("kind") or params.get("writing_phase") or _intent_action(payload))
    agent = str(params.get("oma_agent") or "")
    capability = str(params.get("oma_capability") or kind)
    if not agent:
        from app.domain.worker_execution_policy import resolve_agent_capability

        agent, capability = resolve_agent_capability(kind)

    chapter_index = _chapter_from_state(state)
    policy = policy_for_work_item(kind)
    if not policy:
        get_metrics_service().inc_acceptance_fail("missing_worker_execution_policy")
    bundle = build_fact_bundle(
        state,
        agent=agent,
        capability=capability,
        chapter_index=chapter_index,
        policy=policy,
    )
    state = attach_fact_bundle_to_state(state, bundle)

    payload = dict(state.get("input_payload") or {})
    payload["worker_execution_policy"] = policy.to_dict()
    payload["oma_agent"] = agent
    payload["oma_capability"] = capability
    item_params = dict((item.get("params") or {}))
    item_params["fact_bundle_id"] = bundle.get("fact_bundle_id")
    if item:
        item = {**item, "params": item_params}
        payload["current_work_item"] = item
    wi = dict(payload.get("writing_intent") or {})
    wi["fact_bundle_id"] = bundle.get("fact_bundle_id")
    wi["fact_bundle"] = bundle
    payload["writing_intent"] = wi

    metrics = get_metrics_service()
    metrics.inc_mission_dispatch(agent, capability)

    if getattr(settings, "MISSION_OMA_REQUIRE_FACT_BUNDLE", True) and not bundle.get(
        "fact_bundle_id"
    ):
        get_metrics_service().inc_writing_without_fact_bundle()
        from app.services.mission_oma.failure_recovery import handle_worker_failure

        state = handle_worker_failure(
            state,
            agent=agent,
            capability=capability,
            reason="missing_fact_bundle",
            chapter_index=chapter_index,
        )
    progress = dict(state.get("progress") or {})
    plan = progress.get("work_plan") or {}
    for row in plan.get("items") or []:
        if row.get("id") == item.get("id"):
            row.setdefault("params", {})
            row["params"]["fact_bundle_id"] = bundle.get("fact_bundle_id")
    progress["work_plan"] = plan
    return merge_state(state, input_payload=payload, progress=progress)


def stamp_worker_task_state(state: AgentState) -> AgentState:
    """ADR 10.3 — Worker Task State snapshot."""
    payload = dict(state.get("input_payload") or {})
    item = payload.get("current_work_item") or {}
    bundle = payload.get("fact_bundle") or {}
    worker_state = {
        "work_item_id": item.get("id"),
        "chapter_index": _chapter_from_state(state),
        "capability": payload.get("oma_capability"),
        "agent": payload.get("oma_agent"),
        "fact_bundle_id": bundle.get("fact_bundle_id"),
        "manuscript_snapshot": bundle.get("snapshot_version"),
        "artifact_paths": {
            "body": (state.get("manuscript") or {}).get("body_path"),
            "outline": (state.get("manuscript") or {}).get("outline_path"),
        },
    }
    payload["worker_task_state"] = worker_state
    return merge_state(state, input_payload=payload, oma_worker_state=worker_state)


def _worker_scope_id(state: AgentState) -> str:
    from app.services.execution_control import resolve_worker_id

    return resolve_worker_id(state)


def _check_worker_control(state: AgentState, *, phase: str = "oma_worker") -> None:
    from app.services.execution_control import check_for_control_signal

    check_for_control_signal(
        str(state["task_id"]),
        worker_id=_worker_scope_id(state),
        phase=phase,
        raise_on_pause=True,
        raise_on_cancel=True,
    )


def execute_oma_worker(state: AgentState) -> AgentState:
    """
    Run worker capability after FactBundle construction.
    Delegates to existing writing_node / writing_phases.
    """
    from app.config.settings import settings
    from app.nodes.writing_node import writing_node
    from app.services.mission_executor import _mission_writing_reasoning_summary
    from app.services.observation import attach_observation
    from app.services.writing_phases import run_writing_phase

    decision = state.get("step_decision") or {}
    if (decision.get("params") or {}).get("skip_work_item"):
        from app.services.mission_orchestrator import complete_current_work_item
        from app.services.mission_executor import _mission_writing_reasoning_summary

        return _mission_writing_reasoning_summary(complete_current_work_item(state))

    state = prepare_worker_execution(state)
    state = stamp_worker_task_state(state)
    _check_worker_control(state, phase="oma_worker_entry")
    payload = state.get("input_payload") or {}
    intent = dict(payload.get("writing_intent") or {})
    action = str(intent.get("action") or "")
    chapter_index = _chapter_from_state(state)

    phase_actions = frozenset(
        {
            "review_chapter",
            "polish_chapter",
            "chapter_summary",
            "consistency_check",
        }
    )

    agent = str(payload.get("oma_agent") or "writer")
    capability = str(payload.get("oma_capability") or action)

    with chapter_artifact_lock(state["task_id"], chapter_index, capability):
        try:
            if action in phase_actions:
                current = run_writing_phase(state, intent)
            else:
                current = writing_node(state)
        except Exception as exc:
            from app.services.execution_control import (
                CancelRequested,
                PauseRequested,
                handle_control_exception,
            )

            handled = handle_control_exception(state, exc)
            if handled is not None:
                return handled
            if isinstance(exc, (PauseRequested, CancelRequested)):
                raise
            from app.services.mission_oma.failure_recovery import handle_worker_failure

            current = handle_worker_failure(
                state,
                agent=agent,
                capability=capability,
                reason=str(exc),
                chapter_index=chapter_index,
            )
            if str(current.get("status", "")).endswith("FAILED"):
                return current
            if action in phase_actions:
                current = run_writing_phase(current, intent)
            else:
                current = writing_node(current)

        if str(current.get("status", "")).endswith("FAILED"):
            from app.services.mission_oma.failure_recovery import handle_worker_failure

            current = handle_worker_failure(
                current,
                agent=agent,
                capability=capability,
                reason="worker_failed",
                chapter_index=chapter_index,
            )
            if str(current.get("status", "")).endswith("FAILED"):
                return current

    current = attach_observation(current)
    from app.services.mission_service import update_progress_from_observation
    from app.services.mission_orchestrator import complete_current_work_item

    current = update_progress_from_observation(current)
    if not (decision.get("params") or {}).get("skip_work_item"):
        current = complete_current_work_item(current)

    if str(current.get("status", "")) == "WRITTEN":
        return _mission_writing_reasoning_summary(current)
    return current


def run_chapter_review_worker(
    state: AgentState,
    *,
    chapter_index: int,
) -> dict[str, Any]:
    """Isolated review for parallel batch (one chapter, shared parent state snapshot)."""
    from app.runtime.state import merge_state

    payload = dict(state.get("input_payload") or {})
    payload["writing_intent"] = {
        "enabled": True,
        "action": "review_chapter",
        "chapter_index": chapter_index,
        "source": "oma_parallel",
    }
    local = merge_state(state, input_payload=payload)
    local = prepare_worker_execution(local)
    _check_worker_control(local, phase="chapter_review_worker")
    from app.services.writing_phases import run_writing_phase

    try:
        result_state = run_writing_phase(local, payload["writing_intent"])
    except Exception as exc:
        from app.services.execution_control import (
            CancelRequested,
            PauseRequested,
            handle_control_exception,
        )

        handled = handle_control_exception(local, exc)
        if handled is not None:
            result_state = handled
        elif isinstance(exc, (PauseRequested, CancelRequested)):
            raise
        else:
            raise
    tr = (result_state.get("tool_results") or [{}])[0]
    inner = tr.get("result") if isinstance(tr, dict) else {}
    from app.domain.review_verdict import load_review_verdict

    verdict = load_review_verdict(result_state["task_id"], chapter_index)
    return {
        "chapter_index": chapter_index,
        "status": "COMPLETED",
        "result": inner if isinstance(inner, dict) else {},
        "review_verdict": verdict.to_dict() if verdict and verdict.is_valid() else None,
    }


def enrich_review_with_verdict(state: AgentState, phase_result: dict[str, Any]) -> AgentState:
    """Convert phase review result to ReviewVerdict and persist."""
    from app.domain.review_verdict import ReviewVerdict, save_review_verdict

    payload = state.get("input_payload") or {}
    bundle = payload.get("fact_bundle") or {}
    intent = payload.get("writing_intent") or {}
    chapter = int(intent.get("chapter_index") or phase_result.get("chapter_index") or 0)
    rubric = phase_result.get("chapter_quality") or {}
    rag_meta = {
        "outline_slice": any(s.get("type") == "outline" for s in bundle.get("sources") or []),
        "prev_chapter": any(
            "summary" in str(s.get("ref") or "") for s in bundle.get("sources") or []
        ),
        "rag_used": bool(bundle.get("rag_hit_count")),
        "react_steps": int(payload.get("react_steps") or 0),
        "knowledge_used": bool(bundle.get("rag_hit_count")),
        "fact_bundle_id": str(bundle.get("fact_bundle_id") or ""),
    }
    verdict = ReviewVerdict.from_phase_result(
        chapter_index=chapter,
        phase_result=phase_result,
        rubric_dict=rubric,
        fact_bundle_id=rag_meta["fact_bundle_id"],
        rag_meta=rag_meta,
    )
    if verdict.is_valid():
        save_review_verdict(state["task_id"], verdict)
        from app.services.metrics_service import get_metrics_service

        get_metrics_service().inc_review_verdict(verdict.qualified)
    payload = dict(payload)
    payload["last_review_verdict"] = verdict.to_dict()
    return merge_state(state, input_payload=payload)
