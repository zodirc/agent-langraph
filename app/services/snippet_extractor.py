"""Code-aware and NL snippet extraction (§4.3.1 / §7.6.1)."""

from __future__ import annotations

import re

_CODE_FENCE_RE = re.compile(r"```[\w]*\n(.*?)```", re.DOTALL)
_SENTENCE_RE = re.compile(r"[^.!?。！？\n]+[.!?。！？]?")
_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _tokenize(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text) if len(t) > 1}


def _sentence_score(sentence: str, query_tokens: set[str]) -> float:
    if not query_tokens:
        return 0.0
    lower = sentence.lower()
    hits = sum(1 for t in query_tokens if t in lower)
    return hits / len(query_tokens)


def extract_code_blocks(text: str, query: str, *, max_blocks: int = 2) -> str:
    blocks = _CODE_FENCE_RE.findall(text)
    if not blocks:
        return ""
    q_tokens = _tokenize(query)
    scored = [( _sentence_score(b, q_tokens), b.strip()) for b in blocks if b.strip()]
    scored.sort(key=lambda x: x[0], reverse=True)
    return "\n\n".join(b for _, b in scored[:max_blocks])[:3000]


def extract_snippet(
    text: str,
    query: str,
    *,
    title: str = "",
    purpose: str = "general_grounded",
    max_sentences: int = 5,
    include_summary: bool = False,
) -> tuple[str, tuple[int, int] | None]:
    """
    Extract snippet; code_fix uses code blocks, others use sentence selection.
    When include_summary=True, append short chunk prefix (dual-track observe).
    """
    if purpose == "code_fix" or "```" in text:
        code = extract_code_blocks(text, query)
        if code:
            snippet = f"[{title}] {code}".strip() if title else code
            return snippet[:3000], (0, len(snippet))

    sentences = [s.strip() for s in _SENTENCE_RE.findall(text) if len(s.strip()) > 8]
    if len(sentences) <= max_sentences:
        body = text[:3000]
    else:
        q_tokens = _tokenize(query)
        scored = [(i, _sentence_score(s, q_tokens), s) for i, s in enumerate(sentences)]
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:max_sentences]
        top.sort(key=lambda x: x[0])
        body = " ".join(s for _, _, s in top)

    prefix = f"[{title}] " if title else ""
    snippet = f"{prefix}{body}".strip()
    if include_summary and len(text) > len(snippet) + 100:
        summary = text[:120].replace("\n", " ")
        snippet = f"{snippet}\n[chunk_summary] {summary}..."
    return snippet[:3000], (0, len(snippet))
