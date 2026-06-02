"""Structured markdown chunking for knowledge RAG (chunk-first indexing)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_CODE_BLOCK_BOUNDARY_RE = re.compile(
    r"(?m)^(?:\s*(?:def|class)\s+\w+|\s*function\s+\w+|\s*[A-Z][A-Z0-9_]{2,}\s*[:=])"
)


@dataclass(frozen=True)
class KnowledgeChunkSpec:
    chunk_index: int
    section_title: str
    body: str
    embed_text: str
    token_count: int


def _domain_chunk_limits(domain: str) -> tuple[int, int]:
    from app.config.settings import settings

    domain = (domain or "common").strip().lower()
    if domain == "writing":
        max_chars = int(getattr(settings, "RAG_CHUNK_MAX_CHARS_WRITING", 0)) or int(
            settings.RAG_CHUNK_MAX_CHARS
        )
        overlap = int(getattr(settings, "RAG_CHUNK_OVERLAP_CHARS_WRITING", 0)) or int(
            settings.RAG_CHUNK_OVERLAP_CHARS
        )
    elif domain == "code":
        max_chars = int(getattr(settings, "RAG_CHUNK_MAX_CHARS_CODE", 0)) or max(
            400, int(settings.RAG_CHUNK_MAX_CHARS * 0.75)
        )
        overlap = int(getattr(settings, "RAG_CHUNK_OVERLAP_CHARS_CODE", 0)) or int(
            settings.RAG_CHUNK_OVERLAP_CHARS * 0.5
        )
    else:
        max_chars = int(settings.RAG_CHUNK_MAX_CHARS)
        overlap = int(settings.RAG_CHUNK_OVERLAP_CHARS)
    return max(200, max_chars), max(0, overlap)


def _estimate_tokens(text: str) -> int:
    return max(1, len((text or "").split()))


def _build_embed_text(doc_title: str, section_title: str, body: str) -> str:
    parts = [f"文档标题：{doc_title.strip()}"]
    if section_title.strip():
        parts.append(f"章节：{section_title.strip()}")
    parts.append(f"内容：{body.strip()}")
    return "\n".join(parts)


def _split_paragraphs_with_overlap(
    text: str,
    *,
    max_chars: int,
    overlap_chars: int,
) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if not paragraphs:
        return []
    parts: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if not current:
            return
        parts.append("\n\n".join(current))
        if overlap_chars > 0 and len(current) > 1:
            tail = "\n\n".join(current)
            overlap = tail[-overlap_chars:].lstrip()
            current = [overlap] if overlap else []
            current_len = len(overlap)
        else:
            current = []
            current_len = 0

    for para in paragraphs:
        para_len = len(para) + (2 if current else 0)
        if current and current_len + para_len > max_chars:
            flush()
        if len(para) > max_chars:
            if current:
                flush()
            start = 0
            while start < len(para):
                end = min(len(para), start + max_chars)
                parts.append(para[start:end])
                if end >= len(para):
                    break
                start = max(end - overlap_chars, start + 1)
            continue
        current.append(para)
        current_len += para_len
    flush()
    return parts


def _split_by_headings(content: str) -> list[tuple[str, str]]:
    text = (content or "").strip()
    if not text:
        return []
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [("", text)]
    sections: list[tuple[str, str]] = []
    prefix = text[: matches[0].start()].strip()
    if prefix:
        sections.append(("", prefix))
    for idx, match in enumerate(matches):
        title = match.group(2).strip()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            sections.append((title, body))
    return sections or [("", text)]


def _split_code_blocks(section_body: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    text = (section_body or "").strip()
    if not text:
        return []
    matches = list(_CODE_BLOCK_BOUNDARY_RE.finditer(text))
    if len(matches) < 2 and len(text) <= max_chars:
        return [text]
    if not matches:
        return _split_paragraphs_with_overlap(text, max_chars=max_chars, overlap_chars=overlap_chars)

    blocks: list[str] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        if block:
            blocks.append(block)

    if matches[0].start() > 0:
        prefix = text[: matches[0].start()].strip()
        if prefix:
            blocks.insert(0, prefix)

    normalized: list[str] = []
    for block in blocks:
        if len(block) <= max_chars:
            normalized.append(block)
            continue
        normalized.extend(
            _split_paragraphs_with_overlap(block, max_chars=max_chars, overlap_chars=overlap_chars)
        )
    return normalized or [text]


def chunk_document(
    title: str,
    content: str,
    *,
    domain: str = "common",
    metadata: dict[str, Any] | None = None,
) -> list[KnowledgeChunkSpec]:
    """
    Split markdown-ish knowledge into retrieval chunks with title/section prefix injection.
    """
    _ = metadata
    body = (content or "").strip()
    if not body:
        return []

    max_chars, overlap_chars = _domain_chunk_limits(domain)
    doc_title = (title or "").strip() or "未命名文档"
    specs: list[KnowledgeChunkSpec] = []
    chunk_index = 0

    for section_title, section_body in _split_by_headings(body):
        if domain.strip().lower() == "code":
            pieces = _split_code_blocks(
                section_body, max_chars=max_chars, overlap_chars=overlap_chars
            )
        else:
            pieces = (
                [section_body]
                if len(section_body) <= max_chars
                else _split_paragraphs_with_overlap(
                    section_body, max_chars=max_chars, overlap_chars=overlap_chars
                )
            )
        for piece in pieces:
            piece = piece.strip()
            if not piece:
                continue
            embed = _build_embed_text(doc_title, section_title, piece)
            specs.append(
                KnowledgeChunkSpec(
                    chunk_index=chunk_index,
                    section_title=section_title,
                    body=piece,
                    embed_text=embed,
                    token_count=_estimate_tokens(piece),
                )
            )
            chunk_index += 1

    if not specs:
        embed = _build_embed_text(doc_title, "", body)
        specs.append(
            KnowledgeChunkSpec(
                chunk_index=0,
                section_title="",
                body=body,
                embed_text=embed,
                token_count=_estimate_tokens(body),
            )
        )
    return specs
