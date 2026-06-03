"""Filesystem paths for builtin

seed knowledge markdown (RAG source files)."""

from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def repo_root() -> Path:
    return _REPO_ROOT


def knowledge_content_dir() -> Path:
    """
    Directory of seed markdown files (default: ``<repo>/knowledge``).

    Override via config ``knowledge.content_dir`` or env ``KNOWLEDGE_CONTENT_DIR``
    (absolute path, or path relative to repo root).
    """
    from app.config.settings import settings

    raw = os.environ.get("KNOWLEDGE_CONTENT_DIR", "").strip() or settings.KNOWLEDGE_CONTENT_DIR
    path = Path(raw)
    if path.is_absolute():
        return path
    return _REPO_ROOT / path


def knowledge_domain_dir(domain: str) -> Path:
    from app.config.settings import settings

    key = str(domain or "common").strip().lower() or "common"
    mapped = settings.KNOWLEDGE_DOMAIN_PATHS.get(key) or settings.KNOWLEDGE_DOMAIN_PATHS.get("common") or settings.KNOWLEDGE_CONTENT_DIR
    path = Path(str(mapped))
    if path.is_absolute():
        return path
    return _REPO_ROOT / path


def writing_guidelines_path() -> Path:
    return knowledge_domain_dir("writing") / "writing_guidelines.md"


def prose_voice_format_path() -> Path:
    return knowledge_domain_dir("writing") / "prose_voice_and_txt_format.md"


def code_editing_guidelines_path() -> Path:
    return knowledge_domain_dir("code") / "code_editing_guidelines.md"
