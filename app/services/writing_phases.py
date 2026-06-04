"""Writing mission phases — model-chosen micro-cycle (write

review / polish / …).
The mission_decide LLM picks `params.writing_phase` each step; mission_act maps it to
writing_intent. Phases are optional and on-demand, not a fixed pipeline."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from app.config.settings import settings
from app.domain.mission import StepDecision, StepPolicy
from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.artifact_tools import (
    handle_edit_text_artifact,
    handle_read_text_artifact,
    task_artifact_dir,
)
from app.services.manuscript_context import (
    build_writing_context,
    extract_chapter_text,
    extract_outline_chapter_brief,
    read_body_text,
    read_outline_text,
)
from app.services.manuscript_service import Manuscript, resolve_manuscript
from app.services.mission_orchestrator import orchestration_enabled
from app.services import writing_memory as wm

PHASE_ACTIONS = frozenset(
    {
        "consistency_check",
        "review_chapter",
        "polish_chapter",
        "chapter_summary",
        "arc_checkpoint",
    }
)

STORY_BIBLE_FILENAME = "story_bible.json"
CHAPTER_REVIEWS_FILENAME = "chapter_reviews.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def writing_state(progress: dict[str, Any]) -> dict[str, Any]:
    raw = progress.get("writing_state")
    return dict(raw) if isinstance(raw, dict) else {}


def _save_writing_state(progress: dict[str, Any], ws: dict[str, Any]) -> dict[str, Any]:
    return {**progress, "writing_state": ws, "updated_at": _now_iso()}


def _chapter_key(chapter_index: int) -> str:
    return str(int(chapter_index))


def phases_done_for(ws: dict[str, Any], chapter_index: int) -> list[str]:
    by_ch = ws.get("phases_done") or {}
    if not isinstance(by_ch, dict):
        return []
    return list(by_ch.get(_chapter_key(chapter_index)) or [])


def chapter_summary_already_done(state: AgentState, chapter_index: int) -> bool:
    """True when chapter_summary should not run again for this chapter."""
    progress = state.get("progress") or {}
    ws = writing_state(progress)
    if "chapter_summary" in phases_done_for(ws, chapter_index):
        return True
    task_id = state["task_id"]
    from app.services.writing_memory import get_chapter_outcome

    if get_chapter_outcome(task_id, chapter_index) is not None:
        return True
    bible = load_story_bible(task_id)
    entry = (bible.get("chapters") or {}).get(_chapter_key(chapter_index)) or {}
    if isinstance(entry, dict) and (
        entry.get("summary") or entry.get("chapter_summary") or entry.get("ending_state")
    ):
        return True
    return False


def mark_phase_done(state: AgentState, chapter_index: int, phase: str) -> AgentState:
    progress = dict(state.get("progress") or {})
    ws = writing_state(progress)
    by_ch = dict(ws.get("phases_done") or {})
    key = _chapter_key(chapter_index)
    done = list(by_ch.get(key) or [])
    if phase not in done:
        done.append(phase)
    by_ch[key] = done
    ws["phases_done"] = by_ch
    ws["last_phase"] = phase
    ws["last_chapter"] = int(chapter_index)
    return merge_state(state, progress=_save_writing_state(progress, ws))


def load_story_bible(task_id: str) -> dict[str, Any]:
    """Legacy dict API — backed by schema-constrained StoryBible."""
    return wm.load_story_bible(task_id).to_dict()


def save_story_bible(task_id: str, data: dict[str, Any]) -> None:
    from app.domain.writing_memory_models import StoryBible

    wm.save_story_bible(task_id, StoryBible.from_dict(data))


def load_chapter_reviews(task_id: str) -> dict[str, Any]:
    return wm.load_chapter_reviews(task_id)


def save_chapter_reviews(task_id: str, data: dict[str, Any]) -> None:
    wm.save_chapter_reviews(task_id, data)


def should_use_writing_llm_decide(mission: dict[str, Any]) -> bool:
    if str(mission.get("kind", "")).lower() != "writing":
        return False
    if not orchestration_enabled(mission):
        return False
    if getattr(settings, "MISSION_OMA_DEFAULT_FOR_WRITING", True):
        return bool(getattr(settings, "MISSION_WRITING_LLM_DECIDE", False))
    if getattr(settings, "MISSION_WRITING_LLM_DECIDE", True):
        return True
    return bool(getattr(settings, "MISSION_LLM_DECIDE", False))


def resolve_chapter_index(state: AgentState, params: dict[str, Any]) -> int:
    raw = params.get("chapter_index")
    if raw is not None:
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            pass
    mission = state.get("mission") or {}
    progress = state.get("progress") or {}
    metrics = progress.get("metrics") or {}
    last = int(metrics.get("last_chapter_index") or 0)
    if last > 0:
        return last
    payload = state.get("input_payload") or {}
    task_id = state["task_id"]
    ms = resolve_manuscript(task_id, state.get("manuscript"))
    body = read_body_text(task_id, ms.body_path or "novel.txt", state=state)
    from app.services.manuscript_context import parse_last_chapter_index

    return max(1, parse_last_chapter_index(body))


def build_writing_decide_payload(
    state: AgentState,
    *,
    mission: dict[str, Any],
    progress: dict[str, Any],
    observation: dict[str, Any],
    eval_result: Any,
) -> dict[str, Any]:
    task_id = state["task_id"]
    ms = resolve_manuscript(task_id, state.get("manuscript"))
    structure = {}
    try:
        from app.services.manuscript_context import analyze_manuscript_structure

        structure = analyze_manuscript_structure(
            task_id,
            body_path=ms.body_path or "novel.txt",
            outline_path=ms.outline_path,
            state=state,
        )
    except Exception:
        structure = {}

    ws = writing_state(progress)
    bible = load_story_bible(task_id)
    reviews = load_chapter_reviews(task_id)

    return {
        "mission": mission,
        "progress": progress,
        "observation": observation,
        "mission_control_hint": eval_result.to_dict(),
        "mission_step": state.get("mission_step"),
        "manuscript": ms.to_dict() if hasattr(ms, "to_dict") else state.get("manuscript"),
        "writing_state": ws,
        "story_bible_excerpt": {
            "chapter_keys": list((bible.get("chapters") or {}).keys())[-8:],
            "open_threads": (bible.get("open_threads") or [])[:8],
        },
        "last_review_keys": list((reviews.get("reviews") or {}).keys())[-5:],
        "structure": structure,
        "available_phases": [
            "write_outline",
            "append_body",
            "consistency_check",
            "review_chapter",
            "polish_chapter",
            "chapter_summary",
            "arc_checkpoint",
        ],
    }


def normalize_writing_phase(raw: str) -> str:
    phase = (raw or "").strip().lower()
    aliases = {
        "write": "append_body",
        "write_chapter": "append_body",
        "draft": "append_body",
        "outline": "write_outline",
        "consistency": "consistency_check",
        "review": "review_chapter",
        "polish": "polish_chapter",
        "summary": "chapter_summary",
        "checkpoint": "arc_checkpoint",
    }
    return aliases.get(phase, phase)


def apply_writing_phase_from_decision(state: AgentState) -> AgentState:
    """Map step_decision.params.writing_phase → input_payload.writing_intent."""
    from app.services.mission_steer import steer_requires_planning

    payload = state.get("input_payload") or {}
    if steer_requires_planning(payload):
        return state

    decision = state.get("step_decision") or {}
    if str(decision.get("action")) in ("finish", "pause", "escalate"):
        return state

    params = dict(decision.get("params") or {})
    phase = normalize_writing_phase(str(params.get("writing_phase") or ""))
    if not phase:
        return state

    mission = state.get("mission") or {}
    policy = StepPolicy.from_dict(mission.get("step_policy") or {})
    chapter = resolve_chapter_index(state, params)
    payload = dict(state.get("input_payload") or {})
    notes = str(params.get("notes") or "").strip()

    base: dict[str, Any] = {
        "enabled": True,
        "source": "mission_phase",
        "writing_phase": phase,
        "chapter_index": chapter,
        "phase_notes": notes,
        "mission_step": state.get("mission_step"),
    }

    if phase == "write_outline":
        intent = {
            **base,
            "action": "write_outline",
            "target_chars": int(policy.outline_max_chars),
            "min_chars": int(getattr(settings, "MANUSCRIPT_MIN_OUTLINE_CHARS", 80)),
        }
    elif phase == "append_body":
        intent = {
            **base,
            "action": "append_body",
            "target_chars": int(policy.chars_per_step),
            "min_chars": int(getattr(settings, "MANUSCRIPT_MIN_BODY_CHARS", 200)),
            "chapter_index": chapter,
        }
    elif phase in PHASE_ACTIONS:
        intent = {**base, "action": phase, "chapter_index": chapter}
    else:
        return state

    if notes:
        payload["goal"] = f"{payload.get('goal', '')} [{phase}] {notes}".strip()

    return merge_state(state, input_payload={**payload, "writing_intent": intent})


def _invoke_phase_structured(
    state: AgentState,
    system: str,
    user_payload: dict[str, Any],
    *,
    purpose: str = "reviewing",
) -> dict[str, Any]:
    from app.services.llm_client import invoke_structured

    return invoke_structured(
        purpose,
        system,
        json.dumps(user_payload, ensure_ascii=False),
        trace_state=state,
    )


def _phase_summary_result(phase: str, chapter: int, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool": f"writing_phase:{phase}",
        "status": "ok",
        "result": {"chapter_index": chapter, **data},
    }


def run_writing_phase(state: AgentState, intent: dict[str, Any]) -> AgentState:
    """Execute non-append phases (review, polish, summary, …)."""
    action = str(intent.get("action") or "")
    if action not in PHASE_ACTIONS:
        raise ValueError(f"unknown writing phase action: {action}")

    task_id = state["task_id"]
    chapter = int(intent.get("chapter_index") or resolve_chapter_index(state, {}))
    ms = resolve_manuscript(task_id, state.get("manuscript"))
    body_name = ms.body_path or "novel.txt"
    outline_name = ms.outline_path or "outline.txt"
    body_text = read_body_text(task_id, body_name, state=state)
    chapter_text = extract_chapter_text(body_text, chapter)
    outline_text = read_outline_text(task_id, outline_name, state=state)
    outline_slice = extract_outline_chapter_brief(outline_text, chapter)
    bible = load_story_bible(task_id)
    wctx = build_writing_context(
        task_id=task_id,
        state=state,
        body_filename=body_name,
        outline_filename=outline_name,
        chapter_index=chapter,
    )

    def _detect_outline_change_severity() -> str:
        """
        Prefer outline diff severity carried by the last outline rewrite.
        Fallback: use outline-body alignment change_level if present.
        """
        payload = state.get("input_payload") or {}
        diff = payload.get("outline_diff") or {}
        sev = str(diff.get("severity") or "").lower()
        if sev in ("trivial", "minor", "moderate", "major"):
            return sev
        alignment = payload.get("outline_body_alignment") or {}
        change_level = str(alignment.get("change_level") or "").lower()
        if change_level in ("minor", "moderate", "major"):
            # Normalize to diff severity vocabulary.
            return "minor" if change_level == "minor" else change_level
        return "moderate"

    def _max_micro_tune_rounds(severity: str) -> int:
        # C: trivial/minor = 1, moderate = 2, major = 3
        if severity in ("trivial", "minor"):
            return 1
        if severity == "moderate":
            return 2
        if severity == "major":
            return 3
        return 2

    if action == "consistency_check":
        result = _invoke_phase_structured(
            state,
            "Return JSON: issues (list), severity (low|medium|high), pass (bool), summary (string).",
            {
                "phase": "consistency_check",
                "chapter_index": chapter,
                "chapter_text": chapter_text[:12000] if chapter_text else None,
                "outline_for_chapter": outline_slice,
                "story_bible": bible,
                "writing_context": wctx,
            },
        )
        state = mark_phase_done(state, chapter, "consistency_check")
        return _finish_phase(state, action, chapter, result, ms)

    if action == "review_chapter":
        from app.services.writing_quality import rubric_passes_gate, score_chapter_quality

        prev_text = extract_chapter_text(body_text, chapter - 1) if chapter > 1 else ""
        rubric_dict: dict[str, Any] = {}
        if not chapter_text:
            result = {"issues": ["chapter text empty"], "pass": False, "summary": "no chapter to review"}
        else:
            rubric = score_chapter_quality(
                chapter_text=chapter_text,
                prev_chapter_text=prev_text,
                outline_slice=outline_slice or "",
                story_bible_excerpt=bible,
                trace_state=state,
            )
            llm_review = _invoke_phase_structured(
                state,
                "Return JSON: issues (list of strings), pass (bool), "
                "summary (string), polish_recommended (bool).",
                {
                    "phase": "review_chapter",
                    "chapter_index": chapter,
                    "chapter_text": chapter_text[:14000],
                    "outline_for_chapter": outline_slice,
                    "novel_tail": wctx.get("novel_tail"),
                    "story_bible": bible,
                    "quality_rubric": rubric.to_dict(),
                },
            )
            gate_pass = rubric_passes_gate(rubric)
            gate_pass_final = gate_pass and bool(llm_review.get("pass", True))
            polish_rec = bool(llm_review.get("polish_recommended")) or (
                rubric.duplication_risk > 0.4 or not gate_pass
            )
            rubric_dict = rubric.to_dict()
            result = {
                **llm_review,
                "pass": gate_pass_final,
                "chapter_quality": rubric_dict,
                "polish_recommended": polish_rec,
                "polish_skipped": gate_pass_final and not polish_rec,
            }
        from app.domain.review_verdict import ReviewVerdict, save_review_verdict

        payload = state.get("input_payload") or {}
        bundle = payload.get("fact_bundle") or {}
        rag_meta = {
            "outline_slice": bool(outline_slice),
            "prev_chapter": chapter > 1,
            "rag_used": bool(bundle.get("rag_hit_count")),
            "react_steps": int(payload.get("react_steps") or 0),
            "knowledge_used": bool(bundle.get("rag_hit_count")),
            "memory_used": bool(state.get("memory_hits")),
            "fact_bundle_id": str(
                bundle.get("fact_bundle_id") or payload.get("fact_bundle_id") or ""
            ),
        }
        verdict = ReviewVerdict.from_phase_result(
            chapter_index=chapter,
            phase_result=result,
            rubric_dict=rubric_dict or dict(result.get("chapter_quality") or {}),
            fact_bundle_id=rag_meta["fact_bundle_id"],
            rag_meta=rag_meta,
        )
        if verdict.is_valid():
            save_review_verdict(task_id, verdict)
            result = verdict.to_dict()
        else:
            reviews = load_chapter_reviews(task_id)
            rev_map = dict(reviews.get("reviews") or {})
            rev_map[_chapter_key(chapter)] = {**result, "at": _now_iso()}
            reviews["reviews"] = rev_map
            save_chapter_reviews(task_id, reviews)
        state = mark_phase_done(state, chapter, "review_chapter")
        return _finish_phase(state, action, chapter, result, ms)

    if action == "polish_chapter":
        if not chapter_text:
            raise ValueError(f"polish_chapter: no text for chapter {chapter}")
        # Writing micro-tune loop: repeat polish up to C( severity ) rounds,
        # using quality gate to decide early exit.
        from app.services.writing_quality import rubric_passes_gate, score_chapter_quality
        from app.services.artifact_tools import task_artifact_dir

        reviews = load_chapter_reviews(task_id)
        rev = (reviews.get("reviews") or {}).get(_chapter_key(chapter)) or {}
        severity = _detect_outline_change_severity()
        max_rounds = _max_micro_tune_rounds(severity)

        min_delta_abs = int(getattr(settings, "WRITING_RECONCILE_MIN_PATCH_CHARS", 80))
        rounds_used = 0
        gate_passed = False
        no_progress = False
        last_polished = ""
        last_edit_out: dict[str, Any] = {}
        gate_rubric: dict[str, Any] = {}

        body_path = task_artifact_dir(task_id) / str(body_name)
        for round_idx in range(1, max_rounds + 1):
            rounds_used = round_idx
            # Refresh the latest chapter text for safe incremental edits.
            body_text = read_body_text(task_id, body_name, state=state)
            chapter_text = extract_chapter_text(body_text, chapter)
            if not chapter_text:
                raise ValueError(f"polish_chapter: no text for chapter {chapter}")
            prev_text = extract_chapter_text(body_text, chapter - 1) if chapter > 1 else ""

            before_bytes = body_path.stat().st_size if body_path.exists() else 0
            polish = _invoke_phase_structured(
                state,
                "Return JSON with key polished_text: full revised chapter in Chinese, same header, "
                "fix issues only, keep plot beats.",
                {
                    "phase": "polish_chapter",
                    "chapter_index": chapter,
                    "chapter_text": chapter_text[:14000],
                    "review": rev,
                    "outline_for_chapter": outline_slice,
                },
            )
            polished = str(polish.get("polished_text") or "").strip()
            if len(polished) < 100:
                raise ValueError("polish_chapter: model returned insufficient text")

            edit_out = handle_edit_text_artifact(
                {
                    "task_id": task_id,
                    "filename": body_name,
                    "old_text": chapter_text,
                    "new_text": polished,
                    "replace_all": False,
                }
            )
            ms.body_bytes = int(edit_out.get("bytes") or ms.body_bytes)
            after_bytes = body_path.stat().st_size if body_path.exists() else before_bytes
            delta_abs = abs(after_bytes - before_bytes)
            no_progress = delta_abs < min_delta_abs

            # Heuristic quality gate (avoid extra LLM calls inside the loop).
            updated_body_text = read_body_text(task_id, body_name, state=state)
            updated_chapter_text = extract_chapter_text(updated_body_text, chapter)
            rubric = score_chapter_quality(
                chapter_text=updated_chapter_text,
                prev_chapter_text=prev_text,
                outline_slice=outline_slice or "",
                story_bible_excerpt=bible,
                trace_state=state,
            )
            gate_rubric = rubric.to_dict()
            gate_passed = rubric_passes_gate(rubric)
            last_polished = polished
            last_edit_out = edit_out

            if gate_passed:
                break
            if no_progress and round_idx >= 1 and rounds_used < max_rounds:
                # Micro-tune produced too small a structural change and gate still fails:
                # stop early to avoid endless back-and-forth.
                break

        state = mark_phase_done(state, chapter, "polish_chapter")
        return _finish_phase(
            state,
            action,
            chapter,
            {
                "polished_chars": len(last_polished),
                "edit": last_edit_out,
                "micro_tune_severity": severity,
                "micro_tune_rounds": rounds_used,
                "micro_tune_gate_passed": gate_passed,
                "micro_tune_no_progress": no_progress,
                "micro_tune_gate_rubric": gate_rubric,
            },
            ms,
            tool_results_extra=[
                {"tool": "edit_text_artifact", "status": "ok", "result": last_edit_out},
            ],
        )

    if action == "chapter_summary":
        from app.services.chapter_outcome import extract_chapter_outcome, sync_story_bible_from_outcome

        prev_text = extract_chapter_text(body_text, chapter - 1) if chapter > 1 else ""
        if not chapter_text:
            chapter_text = "(empty)"
        outcome = extract_chapter_outcome(
            task_id=task_id,
            chapter_index=chapter,
            chapter_text=chapter_text,
            outline_slice=outline_slice or "",
            prev_chapter_text=prev_text,
            story_bible=bible,
            outcome_use_llm=True,
            persist=True,
            trace_state=state,
        )
        sync_story_bible_from_outcome(task_id, outcome)
        from app.services.writing_knowledge_index import upsert_chapter_facts_for_outcome

        upsert_chapter_facts_for_outcome(task_id, outcome)
        summary = {
            "summary": outcome.chapter_summary,
            "ending_state": outcome.ending_state,
            "hook_for_next": outcome.hook_for_next,
            "events": [e.to_dict() for e in outcome.events],
            "quality_rubric": outcome.quality_rubric.to_dict()
            if outcome.quality_rubric
            else None,
        }
        state = mark_phase_done(state, chapter, "chapter_summary")
        return _finish_phase(state, action, chapter, summary, ms)

    if action == "arc_checkpoint":
        notes = str(intent.get("phase_notes") or "arc checkpoint")
        bible["checkpoints"] = list(bible.get("checkpoints") or []) + [
            {"chapter": chapter, "notes": notes, "at": _now_iso()}
        ]
        save_story_bible(task_id, bible)
        state = mark_phase_done(state, chapter, "arc_checkpoint")
        return _finish_phase(
            state,
            action,
            chapter,
            {"checkpoint": notes, "hint": "mission_decide may pause for human review"},
            ms,
        )

    raise ValueError(f"unhandled phase: {action}")


def _finish_phase(
    state: AgentState,
    phase: str,
    chapter: int,
    result: dict[str, Any],
    ms: Manuscript,
    *,
    tool_results_extra: Optional[list[dict[str, Any]]] = None,
) -> AgentState:
    tools = [_phase_summary_result(phase, chapter, result)]
    if tool_results_extra:
        tools.extend(tool_results_extra)
    summary = f"【{phase}】第{chapter}章完成"
    if result.get("summary"):
        summary += f"：{result['summary'][:200]}"
    return merge_state(
        state,
        manuscript=ms.to_dict(),
        tool_results=tools,
        status=TaskStatus.WRITTEN.value,
        reasoning_result={
            "summary": summary,
            "confidence": 0.88,
            "risk_level": "LOW",
            "structured": {"writing_phase": phase, "chapter_index": chapter},
        },
        audit_log=append_audit(
            state,
            "writing_phase",
            "success",
            {"phase": phase, "chapter_index": chapter},
        ),
    )


def suggest_writing_phase_fallback(state: AgentState) -> StepDecision:
    """Rule fallback when LLM decide fails — minimal heuristics, prefer model path."""
    mission = state.get("mission") or {}
    progress = state.get("progress") or {}
    metrics = progress.get("metrics") or {}
    task_id = state["task_id"]
    ms = resolve_manuscript(task_id, state.get("manuscript"))
    outline_bytes = int(ms.outline_bytes or 0)
    written = float(metrics.get("written_chars") or 0)
    target = float((mission.get("success_criteria") or {}).get("target") or 0)
    body = read_body_text(task_id, ms.body_path or "novel.txt", state=state)
    from app.services.manuscript_context import parse_last_chapter_index

    last_ch = parse_last_chapter_index(body)
    ws = writing_state(progress)
    done = phases_done_for(ws, last_ch) if last_ch else []

    if outline_bytes < 200:
        return StepDecision(
            action="continue",
            next_executor="subgraph:writing",
            params={"writing_phase": "write_outline"},
            rationale="outline missing",
        )

    if target > 0 and written >= target:
        return StepDecision(action="finish", rationale="target_chars reached")

    next_ch = max(1, last_ch + (1 if body.strip() else 0))
    if last_ch > 0 and "chapter_summary" not in done:
        if not chapter_summary_already_done(state, last_ch):
            return StepDecision(
                action="continue",
                next_executor="subgraph:writing",
                params={"writing_phase": "chapter_summary", "chapter_index": last_ch},
                rationale="extract chapter outcome",
            )
    if last_ch > 0 and "review_chapter" not in done:
        reviews = load_chapter_reviews(task_id)
        rev = (reviews.get("reviews") or {}).get(_chapter_key(last_ch)) or {}
        quality = rev.get("chapter_quality") or {}
        if quality and not quality.get("pass_gate", True):
            return StepDecision(
                action="continue",
                next_executor="subgraph:writing",
                params={"writing_phase": "polish_chapter", "chapter_index": last_ch},
                rationale="quality gate failed — polish",
            )
        return StepDecision(
            action="continue",
            next_executor="subgraph:writing",
            params={"writing_phase": "review_chapter", "chapter_index": last_ch},
            rationale="review latest chapter",
        )

    return StepDecision(
        action="continue",
        next_executor="subgraph:writing",
        params={"writing_phase": "append_body", "chapter_index": next_ch},
        rationale="draft next chapter",
    )
