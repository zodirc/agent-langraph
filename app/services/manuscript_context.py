"""
Manuscript continuation context — tail + outline slice + chapter cursor.

Keeps long-form writing coherent (not fragmented) without relying on vector memory
for full prose; episodic memory stores summaries / story-bible tags separately.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Optional

from app.config.settings import settings
from app.services.artifact_tools import read_artifact_tail, task_artifact_dir
from app.services.manuscript_service import resolve_read_paths

_CHAPTER_HEADER_RE = re.compile(
    r"^#{1,3}\s*第\s*([一二三四五六七八九十百零两\d]+)\s*章",
    re.MULTILINE,
)
_OUTLINE_CHAPTER_RE = re.compile(
    r"(?:^|\n)(?:#{1,3}\s*)?第\s*([一二三四五六七八九十百零两\d]+)\s*章[^\n]*",
    re.MULTILINE,
)

_CN_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "百": 100,
}


def cn_numeral_to_int(raw: str) -> Optional[int]:
    """Best-effort parse of Chinese chapter numerals (e.g. 十一 -> 11)."""
    text = (raw or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "十" in text:
        parts = text.split("十", 1)
        high = _CN_DIGITS.get(parts[0], 1) if parts[0] else 1
        low = _CN_DIGITS.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
        return high * 10 + low
    total = 0
    for ch in text:
        if ch in _CN_DIGITS:
            total = total * 10 + _CN_DIGITS[ch]
    return total if total > 0 else None


def sync_chapter_fields(
    *,
    manuscript: dict[str, Any],
    writing_intent: dict[str, Any],
    last_chapter: int,
    next_chapter: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Align manuscript cursor and writing_intent chapter_index to one value."""
    ms = dict(manuscript)
    intent = dict(writing_intent)
    chapter = max(1, next_chapter)
    ms["chapter_cursor"] = chapter
    ms["last_chapter_index"] = max(0, last_chapter)
    intent["chapter_index"] = chapter
    return ms, intent


def parse_last_chapter_index(body_text: str) -> int:
    """Highest chapter number seen in manuscript body headers."""
    max_idx = 0
    for match in _CHAPTER_HEADER_RE.finditer(body_text or ""):
        value = cn_numeral_to_int(match.group(1))
        if value is not None:
            max_idx = max(max_idx, value)
    return max_idx


def _chapter_header_positions(body_text: str) -> list[tuple[int, int, str]]:
    """(start_offset, chapter_index, header_line) sorted by position."""
    rows: list[tuple[int, int, str]] = []
    for match in _CHAPTER_HEADER_RE.finditer(body_text or ""):
        value = cn_numeral_to_int(match.group(1))
        if value is not None:
            rows.append((match.start(), value, match.group(0).strip()))
    rows.sort(key=lambda r: r[0])
    return rows


def extract_chapter_text(body_text: str, chapter_index: int) -> str:
    """Slice one chapter's prose by header index (empty if not found)."""
    if chapter_index < 1 or not (body_text or "").strip():
        return ""
    positions = _chapter_header_positions(body_text)
    start = None
    end = len(body_text)
    for idx, (offset, num, _header) in enumerate(positions):
        if num == chapter_index:
            start = offset
            if idx + 1 < len(positions):
                end = positions[idx + 1][0]
            break
    if start is None:
        return ""
    return body_text[start:end].strip()


def read_body_text(
    task_id: str,
    filename: str,
    *,
    state: Optional[dict[str, Any]] = None,
) -> str:
    name = resolve_read_paths(state or {}, filename) if state else filename
    path = task_artifact_dir(task_id) / name
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def read_outline_text(
    task_id: str,
    outline_name: str,
    *,
    state: Optional[dict[str, Any]] = None,
) -> str:
    name = resolve_read_paths(state or {}, outline_name) if state else outline_name
    path = task_artifact_dir(task_id) / name
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def extract_outline_chapter_brief(
    outline_text: str,
    chapter_index: int,
    *,
    max_chars: int = 2000,
) -> str:
    """Slice outline section for the target chapter (and light global header)."""
    if not outline_text or chapter_index < 1:
        return ""

    lines = outline_text.splitlines()
    header_lines: list[str] = []
    chapter_lines: list[str] = []
    in_target = False
    target_label = None

    for line in lines:
        m = _OUTLINE_CHAPTER_RE.search(line)
        if m:
            num = cn_numeral_to_int(m.group(1))
            if num == chapter_index:
                in_target = True
                target_label = line.strip()
                chapter_lines = [line]
                continue
            if in_target and num is not None and num > chapter_index:
                break
            if num is not None and num < chapter_index and len(header_lines) < 40:
                header_lines.append(line)
            in_target = False
            continue
        if in_target:
            chapter_lines.append(line)
        elif len(header_lines) < 25 and line.strip():
            header_lines.append(line)

    parts: list[str] = []
    if header_lines:
        parts.append("【作品概要摘录】\n" + "\n".join(header_lines[:25]))
    if chapter_lines:
        label = target_label or f"第{chapter_index}章"
        parts.append(f"【本章大纲：{label}】\n" + "\n".join(chapter_lines))
    text = "\n\n".join(parts).strip()
    if len(text) > max_chars:
        return text[:max_chars] + "\n...(outline truncated)"
    return text


def build_writing_context(
    *,
    task_id: str,
    state: dict[str, Any],
    body_filename: str,
    outline_filename: Optional[str] = None,
    chapter_index: Optional[int] = None,
) -> dict[str, Any]:
    """
    Assemble continuation context: tail prose, outline slice, chapter cursor.
    """
    payload = state.get("input_payload") or {}
    manuscript = payload.get("manuscript") or state.get("manuscript") or {}
    tail_chars = int(
        getattr(settings, "MANUSCRIPT_TAIL_EXCERPT_CHARS", 2400)
    )
    head_chars = int(getattr(settings, "MANUSCRIPT_HEAD_EXCERPT_CHARS", 1200))

    body_name = resolve_read_paths(state, body_filename)
    body_text = read_body_text(task_id, body_name, state=state)
    last_chapter = parse_last_chapter_index(body_text)
    stored_cursor = int(manuscript.get("chapter_cursor") or 0)
    if chapter_index is not None and chapter_index > 0:
        next_chapter = chapter_index
    elif stored_cursor > last_chapter:
        next_chapter = stored_cursor
    else:
        next_chapter = max(1, last_chapter + (1 if body_text.strip() else 0))

    if payload.get("previous_artifact_excerpt"):
        tail = str(payload["previous_artifact_excerpt"])[-tail_chars:]
    else:
        tail = read_artifact_tail(task_id, body_name, max_chars=tail_chars)

    head = ""
    if body_text and head_chars > 0:
        head = body_text[:head_chars]
        if len(body_text) > head_chars:
            head += "\n...(head truncated)"

    outline_name = outline_filename or str(
        payload.get("outline_filename")
        or manuscript.get("outline_path")
        or settings.MANUSCRIPT_DEFAULT_OUTLINE
    )
    outline_full = read_outline_text(task_id, outline_name, state=state)
    outline_slice = extract_outline_chapter_brief(outline_full, next_chapter)

    intent = payload.get("writing_intent") or {}
    action = str(intent.get("action") or "append_body")

    return {
        "body_filename": body_name,
        "outline_filename": outline_name,
        "chapter_index": next_chapter,
        "last_written_chapter": last_chapter,
        "action": action,
        "novel_tail": tail or None,
        "novel_head": head or None,
        "outline_for_chapter": outline_slice or None,
        "body_total_chars": len(body_text),
        "continuation_rules": [
            "Write ONLY the next chapter; do not rewrite earlier chapters.",
            "Follow outline_for_chapter plot beats; stay consistent with novel_tail.",
            "Resolve foreshadowing planted in novel_tail when outline requires it.",
            "End with a single chapter footer like （第N章完） matching chapter_index.",
        ],
    }


def is_near_duplicate_append(
    tail: str,
    new_content: str,
    *,
    threshold: float = 0.82,
    compare_chars: int = 900,
) -> tuple[bool, float]:
    """Detect looped/regenerated tail (e.g. repeated Munich scene)."""
    a = (tail or "").strip()[-compare_chars:]
    b = (new_content or "").strip()[:compare_chars]
    if len(a) < 80 or len(b) < 80:
        return False, 0.0
    ratio = SequenceMatcher(None, a, b).ratio()
    return ratio >= threshold, ratio


def split_paragraphs(text: str, *, min_len: int = 30) -> list[str]:
    """Split prose into paragraphs (blank-line separated, min length filter)."""
    raw = re.split(r"\n\s*\n+", (text or "").strip())
    return [p.strip() for p in raw if p.strip() and len(p.strip()) >= min_len]


def analyze_manuscript_structure(
    task_id: str,
    *,
    body_path: str,
    outline_path: Optional[str] = None,
    state: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Read-only analysis for planning / get_manuscript_context tool."""
    body_name = resolve_read_paths(state or {}, body_path) if state else body_path
    body_text = read_body_text(task_id, body_name, state=state)
    last_ch = parse_last_chapter_index(body_text)
    tail = read_artifact_tail(
        task_id,
        body_name,
        max_chars=int(getattr(settings, "MANUSCRIPT_TAIL_EXCERPT_CHARS", 2400)),
    )
    chapters = [
        {
            "label": match.group(0).strip(),
            "index": cn_numeral_to_int(match.group(1)),
            "offset": match.start(),
        }
        for match in _CHAPTER_HEADER_RE.finditer(body_text)
    ]
    outline_brief = ""
    if outline_path:
        outline_text = read_outline_text(task_id, outline_path, state=state)
        outline_brief = extract_outline_chapter_brief(
            outline_text, max(1, last_ch + 1), max_chars=1500
        )
    return {
        "body_path": body_name,
        "outline_path": outline_path,
        "total_chars": len(body_text),
        "chapter_count": len(chapters),
        "last_chapter_index": last_ch,
        "next_chapter_index": max(1, last_ch + (1 if body_text.strip() else 0)),
        "chapters": chapters[-12:],
        "tail_excerpt": tail,
        "outline_next_chapter": outline_brief,
        "paragraph_count": len(split_paragraphs(body_text)),
    }
