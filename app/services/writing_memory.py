"""Writing Memory tier — isolated persistence for long-form narrative state."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from app.domain.writing_memory_models import (
    ChapterOutcome,
    ChapterQualityRubric,
    NarrativeEvent,
    StoryBible,
)
from app.services.artifact_tools import task_artifact_dir

WRITING_MEMORY_DIR = "writing_memory"
STORY_BIBLE_FILENAME = "story_bible.json"
CHAPTER_OUTCOMES_FILENAME = "chapter_outcomes.jsonl"
STORY_EVENTS_FILENAME = "story_events.jsonl"
OPEN_LOOPS_FILENAME = "open_loops.json"
STYLE_CONTRACT_FILENAME = "style_contract.json"
CHAPTER_REVIEWS_FILENAME = "chapter_reviews.json"
LEGACY_STORY_BIBLE = "story_bible.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def writing_memory_dir(task_id: str) -> Path:
    path = task_artifact_dir(task_id) / WRITING_MEMORY_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if data is not None else default
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
        except json.JSONDecodeError:
            continue
    return rows


def load_story_bible(task_id: str) -> StoryBible:
    wm = writing_memory_dir(task_id) / STORY_BIBLE_FILENAME
    if wm.exists():
        return StoryBible.from_dict(_read_json(wm, {}))
    legacy = task_artifact_dir(task_id) / LEGACY_STORY_BIBLE
    if legacy.exists():
        bible = StoryBible.from_dict(_read_json(legacy, {}))
        save_story_bible(task_id, bible)
        return bible
    return StoryBible()


def save_story_bible(task_id: str, bible: StoryBible) -> None:
    bible.version = int(bible.version or 0) + 1
    bible.updated_at = _now_iso()
    data = bible.to_dict()
    _write_json(writing_memory_dir(task_id) / STORY_BIBLE_FILENAME, data)
    _write_json(task_artifact_dir(task_id) / LEGACY_STORY_BIBLE, data)


def load_chapter_outcomes(task_id: str, *, limit: int = 50) -> list[ChapterOutcome]:
    path = writing_memory_dir(task_id) / CHAPTER_OUTCOMES_FILENAME
    rows = _read_jsonl(path)
    outcomes = [ChapterOutcome.from_dict(r) for r in rows[-limit:]]
    return outcomes


def append_chapter_outcome(task_id: str, outcome: ChapterOutcome) -> None:
    _append_jsonl(
        writing_memory_dir(task_id) / CHAPTER_OUTCOMES_FILENAME,
        {**outcome.to_dict(), "at": _now_iso()},
    )
    for event in outcome.events:
        append_narrative_event(task_id, event)


def append_narrative_event(task_id: str, event: NarrativeEvent) -> None:
    row = event.to_dict()
    if not row.get("created_at"):
        row["created_at"] = _now_iso()
    _append_jsonl(writing_memory_dir(task_id) / STORY_EVENTS_FILENAME, row)


def load_narrative_events(task_id: str) -> list[NarrativeEvent]:
    path = writing_memory_dir(task_id) / STORY_EVENTS_FILENAME
    return [NarrativeEvent.from_dict(r) for r in _read_jsonl(path)]


def load_open_loops(task_id: str) -> list[dict[str, Any]]:
    bible = load_story_bible(task_id)
    return [o.to_dict() for o in bible.open_loops]


def load_style_contract(task_id: str) -> dict[str, Any]:
    path = writing_memory_dir(task_id) / STYLE_CONTRACT_FILENAME
    if path.exists():
        return dict(_read_json(path, {}) or {})
    bible = load_story_bible(task_id)
    return bible.style_contract.to_dict()


def save_style_contract(task_id: str, contract: dict[str, Any]) -> None:
    _write_json(writing_memory_dir(task_id) / STYLE_CONTRACT_FILENAME, contract)
    bible = load_story_bible(task_id)
    from app.domain.writing_memory_models import StyleContract

    bible.style_contract = StyleContract.from_dict(contract)
    save_story_bible(task_id, bible)


def load_chapter_reviews(task_id: str) -> dict[str, Any]:
    wm = writing_memory_dir(task_id) / CHAPTER_REVIEWS_FILENAME
    if wm.exists():
        return _read_json(wm, {"reviews": {}})
    legacy = task_artifact_dir(task_id) / CHAPTER_REVIEWS_FILENAME
    return _read_json(legacy, {"reviews": {}})


def save_chapter_reviews(task_id: str, data: dict[str, Any]) -> None:
    _write_json(writing_memory_dir(task_id) / CHAPTER_REVIEWS_FILENAME, data)
    _write_json(task_artifact_dir(task_id) / CHAPTER_REVIEWS_FILENAME, data)


def get_chapter_outcome(task_id: str, chapter_index: int) -> Optional[ChapterOutcome]:
    for outcome in reversed(load_chapter_outcomes(task_id, limit=200)):
        if outcome.chapter_index == chapter_index:
            return outcome
    return None


def backup_chapter_text(task_id: str, chapter_index: int, text: str) -> str:
    backup_dir = writing_memory_dir(task_id) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    name = f"chapter_{chapter_index}_{_now_iso().replace(':', '-')}.txt"
    path = backup_dir / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def restore_chapter_from_backup(task_id: str, backup_path: str) -> str:
    path = Path(backup_path)
    if not path.is_absolute():
        path = writing_memory_dir(task_id) / "backups" / path.name
    return path.read_text(encoding="utf-8") if path.exists() else ""


def update_writing_memory(
    task_id: str,
    *,
    kind: Literal[
        "chapter_outcome",
        "story_bible",
        "narrative_event",
        "chapter_review",
        "style_contract",
    ],
    payload: dict[str, Any],
) -> None:
    if kind == "chapter_outcome":
        append_chapter_outcome(task_id, ChapterOutcome.from_dict(payload))
    elif kind == "story_bible":
        save_story_bible(task_id, StoryBible.from_dict(payload))
    elif kind == "narrative_event":
        append_narrative_event(task_id, NarrativeEvent.from_dict(payload))
    elif kind == "chapter_review":
        data = load_chapter_reviews(task_id)
        rev_map = dict(data.get("reviews") or {})
        ch = str(payload.get("chapter_index") or "")
        rev_map[ch] = payload
        data["reviews"] = rev_map
        save_chapter_reviews(task_id, data)
    elif kind == "style_contract":
        save_style_contract(task_id, payload)


def estimate_tokens(text: str) -> int:
    """Rough token estimate for Chinese-heavy prose."""
    if not text:
        return 0
    return max(1, len(text) // 2)


def truncate_to_token_budget(text: str, max_tokens: int) -> str:
    if max_tokens <= 0 or not text:
        return ""
    max_chars = max_tokens * 2
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...(truncated)"


def activate_story_bible_entries(
    bible: StoryBible,
    *,
    outline_text: str = "",
    context_text: str = "",
    max_tokens: int = 1200,
) -> list[dict[str, Any]]:
    """Keyword-activated Story Bible retrieval (L3)."""
    haystack = f"{outline_text}\n{context_text}".lower()
    scored: list[tuple[int, StoryBibleEntry]] = []
    for entry in bible.entries.values():
        if entry.status != "active":
            continue
        hit = False
        for kw in entry.activation_keywords:
            if kw and kw.lower() in haystack:
                hit = True
                break
        if not hit and entry.display_name and entry.display_name.lower() in haystack:
            hit = True
        if hit:
            scored.append((entry.priority, entry))
    scored.sort(key=lambda x: (-x[0], x[1].key))
    selected: list[dict[str, Any]] = []
    used = 0
    for _prio, entry in scored:
        block = f"【{entry.display_name}】{entry.content}"
        cost = estimate_tokens(block)
        if used + cost > max_tokens:
            continue
        selected.append(entry.to_dict())
        used += cost
    return selected
