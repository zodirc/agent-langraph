"""Revision fast-path boundary stream + low-confidence one-shot clarification."""

from __future__ import annotations

from typing import Any

from app.config.settings import settings
from app.domain.revision_intent import RevisionIntent
from app.runtime.state import AgentState, merge_state


def _revision_cfg() -> dict[str, Any]:
    cfg = getattr(settings, "REVISION_CONFIG", {}) or {}
    return cfg if isinstance(cfg, dict) else {}


def scope_resolution_confidence(revision_intent: dict[str, Any] | RevisionIntent | None) -> float:
    """Higher when edits are directly executable without fuzzy section resolution."""
    if revision_intent is None:
        return 0.0
    ri = (
        revision_intent
        if isinstance(revision_intent, RevisionIntent)
        else RevisionIntent.from_dict(revision_intent)
    )
    if ri is None:
        return 0.0
    if ri.confidence > 0:
        return float(ri.confidence)
    if ri.edits and any(e.old_text for e in ri.edits):
        return 0.92
    if ri.edits and any(e.start_line is not None for e in ri.edits):
        return 0.88
    if ri.target_sections:
        return 0.62 if len(ri.target_sections) >= 2 else 0.72
    if ri.revision_scope in ("chapter", "paragraph", "section"):
        return 0.55
    return 0.35


def clarification_threshold() -> float:
    return float(_revision_cfg().get("scope_clarification_threshold", 0.45))


def needs_revision_clarification(revision_intent: dict[str, Any] | RevisionIntent | None) -> bool:
    return scope_resolution_confidence(revision_intent) < clarification_threshold()


def build_revision_boundary_text(revision_intent: dict[str, Any] | RevisionIntent) -> str:
    ri = (
        revision_intent
        if isinstance(revision_intent, RevisionIntent)
        else RevisionIntent.from_dict(revision_intent)
    )
    if ri is None:
        return ""
    sections = "、".join(ri.target_sections) if ri.target_sections else "（待定位）"
    constraints = "、".join(ri.constraints) if ri.constraints else "无"
    preserve = "、".join(ri.preserve_requirements) if ri.preserve_requirements else "无"
    return (
        f"【修订范围】工件: {ri.artifact_filename} ({ri.artifact_role})\n"
        f"范围: {ri.revision_scope} — {sections}\n"
        f"动作: {ri.operation_type}\n"
        f"保护边界: {preserve}；约束: {constraints}\n"
        f"输出: {ri.output_mode}；完成后: {ri.completion_policy}"
    )


def build_revision_clarification_question(revision_intent: dict[str, Any] | RevisionIntent) -> str:
    ri = (
        revision_intent
        if isinstance(revision_intent, RevisionIntent)
        else RevisionIntent.from_dict(revision_intent)
    )
    artifact = ri.artifact_filename if ri else "稿件"
    return (
        f"我需要确认修订范围：要改的是 {artifact} 的哪一段？"
        "请说明章节/段落，或粘贴要改的原句。"
    )


def stream_revision_boundary(
    *,
    task_id: str,
    revision_intent: dict[str, Any] | RevisionIntent,
    node: str = "revision_fast_path",
) -> None:
    text = build_revision_boundary_text(revision_intent)
    if not text:
        return
    try:
        from app.services.stream_progress import report_writing_delta

        report_writing_delta(
            node=node,
            phase="revision_boundary",
            text=text,
            filename=(
                revision_intent.get("artifact_filename")
                if isinstance(revision_intent, dict)
                else revision_intent.artifact_filename
            ),
        )
    except Exception:
        pass
    try:
        from app.runtime.state_field_access import progress_from_state, set_progress_on_state
        from app.services.state_store import get_state_store

        stored = get_state_store().load(task_id) or {}
        progress = dict(progress_from_state(stored) or {})
        progress["revision_boundary"] = text[:4000]
        get_state_store().save(
            set_progress_on_state({**stored, "task_id": task_id}, progress)
        )
    except Exception:
        pass


def maybe_apply_revision_clarification(state: AgentState) -> AgentState:
    """One-shot scope clarification when revision resolution confidence is low."""
    payload = dict(state.get("input_payload") or {})
    if not payload.get("is_revision"):
        return state
    if payload.get("revision_clarification_pending") or payload.get("steer_intent_pending_confirm"):
        return state
    raw = payload.get("revision_intent") or (state.get("intent_observation") or {}).get(
        "revision_intent"
    )
    if not isinstance(raw, dict) or not raw:
        return state
    if not needs_revision_clarification(raw):
        return state

    question = build_revision_clarification_question(raw)
    payload["revision_clarification_pending"] = True
    payload["revision_clarification_question"] = question
    payload["steer_intent_pending_confirm"] = True
    payload["steer_intent_confirmation"] = {
        "summary": question,
        "kind": "revision_clarification",
        "confidence": scope_resolution_confidence(raw),
    }
    from app.services.turn_event_log import record_turn_event

    updated = merge_state(
        state,
        input_payload=payload,
        review_required=True,
        reasoning_result={
            "summary": question,
            "confidence": scope_resolution_confidence(raw),
            "risk_level": "LOW",
            "structured": {"source": "revision_clarification", "kind": "clarification"},
        },
    )
    return record_turn_event(
        updated,
        "revision_clarification_requested",
        "revision_boundary",
        "planning",
        {"question": question[:240]},
    )
