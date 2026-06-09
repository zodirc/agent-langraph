"""Chapter / paragraph / sentence locator → line ranges or anchor text."""

from __future__ import annotations

import re
from typing import Any

_CHAPTER_HEADING_RE = re.compile(
    r"^(?:#{1,6}\s*)?(?:第\s*(\d+|[一二三四五六七八九十百千]+)\s*章[^\n]*)",
    re.MULTILINE,
)
_SECTION_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_PARAGRAPH_INDEX_RE = re.compile(r"第\s*(\d+)\s*段")
_SENTENCE_END_RE = re.compile(r"[。！？.!?]+")


def resolve_sections(
    content: str,
    target_sections: list[str],
) -> list[tuple[int, int, str]]:
    """
    Resolve target_sections to (start_line, end_line, anchor_text).

    Priority: explicit old_text in section label > line range > chapter heading > paragraph index.
    """
    if not content or not target_sections:
        return []

    lines = content.splitlines()
    if not lines:
        return []

    chapter_spans = _chapter_spans(lines)
    results: list[tuple[int, int, str]] = []

    chapter_label = ""
    paragraph_idx: int | None = None
    sentence_idx: int | None = None
    explicit_old = ""

    for section in target_sections:
        text = str(section or "").strip()
        if not text:
            continue
        m = _PARAGRAPH_INDEX_RE.search(text)
        if m:
            paragraph_idx = int(m.group(1))
            continue
        sm = re.search(r"第\s*(\d+)\s*句", text)
        if sm:
            sentence_idx = int(sm.group(1))
            continue
        if len(text) >= 6 and ("“" in text or '"' in text or "'" in text):
            explicit_old = text.strip(""""'「」""")
            continue
        if re.search(r"第\s*(\d+|[一二三四五六七八九十百千]+)\s*章", text):
            chapter_label = text
            continue
        if not chapter_label:
            chapter_label = text

    start_line = 1
    end_line = len(lines)

    if chapter_label:
        span = _match_chapter(chapter_spans, chapter_label, lines)
        if span:
            start_line, end_line = span

    if paragraph_idx is not None:
        p_span = _paragraph_span(lines, start_line, end_line, paragraph_idx)
        if p_span:
            start_line, end_line = p_span

    anchor = ""
    if explicit_old:
        anchor = explicit_old
    elif sentence_idx is not None:
        anchor = _sentence_at(lines, start_line, end_line, sentence_idx)
    elif paragraph_idx is not None:
        anchor = "\n".join(lines[start_line - 1 : end_line]).strip()[:400]

    results.append((start_line, end_line, anchor))
    return results


def _chapter_spans(lines: list[str]) -> list[tuple[int, int, str]]:
    headings: list[tuple[int, str]] = []
    for idx, line in enumerate(lines, start=1):
        if _CHAPTER_HEADING_RE.match(line.strip()) or _SECTION_HEADING_RE.match(line.strip()):
            headings.append((idx, line.strip()))
    if not headings:
        return []
    spans: list[tuple[int, int, str]] = []
    for i, (line_no, label) in enumerate(headings):
        end = headings[i + 1][0] - 1 if i + 1 < len(headings) else len(lines)
        spans.append((line_no, end, label))
    return spans


def _match_chapter(
    chapter_spans: list[tuple[int, int, str]],
    label: str,
    lines: list[str],
) -> tuple[int, int] | None:
    norm_label = _normalize_label(label)
    for start, end, heading in chapter_spans:
        if norm_label in _normalize_label(heading) or _normalize_label(heading) in norm_label:
            return start, end
    m = re.search(r"第\s*(\d+)", label)
    if m:
        num = m.group(1)
        for start, end, heading in chapter_spans:
            if re.search(rf"第\s*{num}\s*章", heading):
                return start, end
    return None


def _paragraph_span(
    lines: list[str],
    chapter_start: int,
    chapter_end: int,
    paragraph_index: int,
) -> tuple[int, int] | None:
    chunk = lines[chapter_start - 1 : chapter_end]
    paragraphs: list[tuple[int, int]] = []
    start = chapter_start
    buf: list[str] = []
    for offset, line in enumerate(chunk):
        abs_line = chapter_start + offset
        if not line.strip():
            if buf:
                paragraphs.append((start, abs_line - 1))
                buf = []
                start = abs_line + 1
            else:
                start = abs_line + 1
        else:
            if not buf:
                start = abs_line
            buf.append(line)
    if buf:
        paragraphs.append((start, chapter_start + len(chunk) - 1))
    if paragraph_index < 1 or paragraph_index > len(paragraphs):
        return None
    return paragraphs[paragraph_index - 1]


def _sentence_at(
    lines: list[str],
    start_line: int,
    end_line: int,
    sentence_index: int,
) -> str:
    text = "\n".join(lines[start_line - 1 : end_line])
    sentences = [s.strip() for s in _SENTENCE_END_RE.split(text) if s.strip()]
    if sentence_index < 1 or sentence_index > len(sentences):
        return ""
    return sentences[sentence_index - 1]


def _normalize_label(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())
