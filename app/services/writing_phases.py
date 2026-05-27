"""
Writing mission phases — model-chosen micro-cycle (write / review / polish / …).

The mission_decide LLM picks `params.writing_phase` each step; mission_act maps it to
writing_intent. Phases are optional and on-demand, not a fixed pipeline.
"""

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


def _story_bible_path(task_id: str) -> Any:
    return task_artifact_dir(task_id) / STORY_BIBLE_FILENAME


def load_story_bible(task_id: str) -> dict[str, Any]:
    path = _story_bible_path(task_id)
    if not path.exists():
        return {"chapters": {}, "characters": {}, "open_threads": [], "updated_at": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"chapters": {}}
    except (json.JSONDecodeError, OSError):
        return {"chapters": {}, "characters": {}, "open_threads": []}


def save_story_bible(task_id: str, data: dict[str, Any]) -> None:
    data = {**data, "updated_at": _now_iso()}
    _story_bible_path(task_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_chapter_reviews(task_id: str) -> dict[str, Any]:
    path = task_artifact_dir(task_id) / CHAPTER_REVIEWS_FILENAME
    if not path.exists():
        return {"reviews": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"reviews": {}}
    except (json.JSONDecodeError, OSError):
        return {"reviews": {}}


def save_chapter_reviews(task_id: str, data: dict[str, Any]) -> None:
    path = task_artifact_dir(task_id) / CHAPTER_REVIEWS_FILENAME
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def should_use_writing_llm_decide(mission: dict[str, Any]) -> bool:
    if str(mission.get("kind", "")).lower() != "writing":
        return False
    if not orchestration_enabled(mission):
        return False
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


def _invoke_phase_structured(system: str, user_payload: dict[str, Any]) -> dict[str, Any]:
    from app.services.llm_client import invoke_structured

    return invoke_structured(
        "reflection",
        system,
        json.dumps(user_payload, ensure_ascii=False),
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

    if action == "consistency_check":
        result = _invoke_phase_structured(
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
        if not chapter_text:
            result = {"issues": ["chapter text empty"], "pass": False, "summary": "no chapter to review"}
        else:
            result = _invoke_phase_structured(
                "Return JSON: issues (list of strings), pass (bool), "
                "summary (string), polish_recommended (bool).",
                {
                    "phase": "review_chapter",
                    "chapter_index": chapter,
                    "chapter_text": chapter_text[:14000],
                    "outline_for_chapter": outline_slice,
                    "novel_tail": wctx.get("novel_tail"),
                    "story_bible": bible,
                },
            )
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
        reviews = load_chapter_reviews(task_id)
        rev = (reviews.get("reviews") or {}).get(_chapter_key(chapter)) or {}
        polish = _invoke_phase_structured(
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
        state = mark_phase_done(state, chapter, "polish_chapter")
        return _finish_phase(
            state,
            action,
            chapter,
            {"polished_chars": len(polished), "edit": edit_out},
            ms,
            tool_results_extra=[
                {"tool": "edit_text_artifact", "status": "ok", "result": edit_out},
            ],
        )

    if action == "chapter_summary":
        if not chapter_text:
            chapter_text = "(empty)"
        summary = _invoke_phase_structured(
            "Return JSON: summary (200-400 Chinese chars), characters (list), "
            "open_threads (list), facts (list of key plot facts).",
            {
                "phase": "chapter_summary",
                "chapter_index": chapter,
                "chapter_text": chapter_text[:10000],
                "outline_for_chapter": outline_slice,
            },
        )
        chapters = dict(bible.get("chapters") or {})
        chapters[_chapter_key(chapter)] = {**summary, "at": _now_iso()}
        bible["chapters"] = chapters
        threads = list(bible.get("open_threads") or [])
        for t in summary.get("open_threads") or []:
            ts = str(t).strip()
            if ts and ts not in threads:
                threads.append(ts)
        bible["open_threads"] = threads[-40:]
        save_story_bible(task_id, bible)
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
    if last_ch > 0 and "review_chapter" not in done:
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
