"""Chapter outcome extraction and event-sourced story state."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from app.domain.writing_memory_models import (
    ChapterOutcome,
    ChapterQualityRubric,
    NarrativeEvent,
    StoryState,
)
from app.services.writing_memory import append_chapter_outcome, load_narrative_events
from app.services.writing_quality import score_chapter_quality


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def rebuild_story_state(events: list[NarrativeEvent]) -> StoryState:
    state = StoryState()
    for event in sorted(events, key=lambda e: (e.chapter_index, e.created_at)):
        state.apply(event)
    return state


def rollback_to_chapter(events: list[NarrativeEvent], chapter: int) -> StoryState:
    filtered = [e for e in events if e.chapter_index <= chapter]
    return rebuild_story_state(filtered)


def _events_from_llm_payload(chapter_index: int, raw: dict[str, Any]) -> list[NarrativeEvent]:
    events: list[NarrativeEvent] = []
    for item in raw.get("events") or raw.get("facts") or []:
        if isinstance(item, str):
            events.append(
                NarrativeEvent(
                    event_id=uuid4().hex,
                    chapter_index=chapter_index,
                    event_type="plot_point_advanced",
                    subject="plot",
                    detail=item.strip(),
                    created_at=_now_iso(),
                )
            )
        elif isinstance(item, dict):
            et = str(item.get("event_type") or "plot_point_advanced")
            events.append(
                NarrativeEvent(
                    event_id=str(item.get("event_id") or uuid4().hex),
                    chapter_index=chapter_index,
                    event_type=et,  # type: ignore[arg-type]
                    subject=str(item.get("subject") or ""),
                    detail=str(item.get("detail") or item.get("text") or ""),
                    metadata=dict(item.get("metadata") or {}),
                    created_at=_now_iso(),
                )
            )
    for t in raw.get("open_threads") or []:
        ts = str(t).strip()
        if ts:
            events.append(
                NarrativeEvent(
                    event_id=uuid4().hex,
                    chapter_index=chapter_index,
                    event_type="foreshadow_planted",
                    subject=ts[:80],
                    detail=ts,
                    created_at=_now_iso(),
                )
            )
    return events


def extract_chapter_outcome(
    *,
    task_id: str,
    chapter_index: int,
    chapter_text: str,
    outline_slice: str = "",
    prev_chapter_text: str = "",
    story_bible: Optional[dict[str, Any]] = None,
    quality_rubric: Optional[ChapterQualityRubric] = None,
    use_llm: bool = True,
    persist: bool = True,
) -> ChapterOutcome:
    rubric = quality_rubric
    if rubric is None:
        rubric = score_chapter_quality(
            chapter_text=chapter_text,
            prev_chapter_text=prev_chapter_text,
            outline_slice=outline_slice,
            story_bible_excerpt=story_bible,
            use_llm=use_llm,
        )

    summary = ""
    ending = ""
    hook = ""
    events: list[NarrativeEvent] = []

    if use_llm and len((chapter_text or "").strip()) >= 50:
        try:
            from app.services.llm_client import invoke_structured

            system = (
                "Return JSON: chapter_summary (<=200 Chinese chars), ending_state, "
                "hook_for_next, events (list of {event_type, subject, detail})."
            )
            payload = {
                "chapter_index": chapter_index,
                "chapter_text": (chapter_text or "")[:10000],
                "outline_for_chapter": (outline_slice or "")[:2500],
            }
            raw = invoke_structured(
                "reflection",
                system,
                json.dumps(payload, ensure_ascii=False),
            )
            summary = str(raw.get("chapter_summary") or raw.get("summary") or "")[:400]
            ending = str(raw.get("ending_state") or "")[:300]
            hook = str(raw.get("hook_for_next") or raw.get("hook") or "")[:300]
            events = _events_from_llm_payload(chapter_index, raw)
        except Exception:
            pass

    if not summary:
        summary = (chapter_text or "").strip()[:200]
    if not ending:
        ending = summary[-120:] if summary else ""
    if not hook and outline_slice:
        hook = outline_slice.splitlines()[-1][:200] if outline_slice else ""
    if not events and summary:
        events = [
            NarrativeEvent(
                event_id=uuid4().hex,
                chapter_index=chapter_index,
                event_type="plot_point_advanced",
                subject=f"第{chapter_index}章",
                detail=summary[:180],
                created_at=_now_iso(),
            )
        ]

    outcome = ChapterOutcome(
        chapter_index=chapter_index,
        events=events,
        chapter_summary=summary,
        ending_state=ending,
        hook_for_next=hook,
        quality_rubric=rubric,
    )
    if persist:
        append_chapter_outcome(task_id, outcome)
    return outcome


def sync_story_bible_from_outcome(
    task_id: str,
    outcome: ChapterOutcome,
    *,
    chapter_summary_raw: Optional[dict[str, Any]] = None,
) -> None:
    from app.services.writing_memory import load_story_bible, save_story_bible

    bible = load_story_bible(task_id)
    key = str(outcome.chapter_index)
    bible.chapters[key] = {
        "summary": outcome.chapter_summary,
        "ending_state": outcome.ending_state,
        "hook_for_next": outcome.hook_for_next,
        "at": _now_iso(),
        **(chapter_summary_raw or {}),
    }
    for event in outcome.events:
        if event.event_type == "foreshadow_planted" and event.subject:
            from app.domain.writing_memory_models import OpenLoop

            bible.open_loops.append(
                OpenLoop(
                    loop_id=event.event_id,
                    description=event.detail or event.subject,
                    planted_chapter=outcome.chapter_index,
                )
            )
    bible.open_loops = bible.open_loops[-40:]
    save_story_bible(task_id, bible)
