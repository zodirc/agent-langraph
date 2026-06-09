"""RevisionIntent → edit_text_artifact / read_text_artifact parameter bridge."""

from __future__ import annotations

from typing import Any

from app.domain.revision_intent import RevisionEdit, RevisionIntent
from app.services.artifact_tools import _safe_filename


def revision_intent_executable(revision_intent: dict[str, Any] | RevisionIntent | None) -> bool:
    """True when revision can run without full planning LLM."""
    ri = _coerce_intent(revision_intent)
    if ri is None:
        return False
    if ri.revision_scope == "full" and ri.artifact_filename:
        return True
    if ri.edits:
        for edit in ri.edits:
            if edit.old_text or (edit.start_line is not None and edit.end_line is not None):
                return True
    return bool(ri.target_sections)


def revision_needs_scope_resolution(revision_intent: dict[str, Any] | RevisionIntent | None) -> bool:
    ri = _coerce_intent(revision_intent)
    if ri is None:
        return False
    if revision_intent_executable(ri.to_dict()):
        return not any(
            e.old_text or (e.start_line is not None)
            for e in ri.edits
        ) and bool(ri.target_sections)
    return bool(ri.target_sections)


def _coerce_intent(
    revision_intent: dict[str, Any] | RevisionIntent | None,
) -> RevisionIntent | None:
    if isinstance(revision_intent, RevisionIntent):
        return revision_intent
    return RevisionIntent.from_dict(revision_intent)


def revision_intent_to_edit_params(
    revision_intent: dict[str, Any] | RevisionIntent,
    task_id: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Map RevisionIntent fields to handle_edit_text_artifact params."""
    ri = _coerce_intent(revision_intent)
    if ri is None:
        raise ValueError("invalid revision_intent")
    filename = _safe_filename(ri.artifact_filename)
    params: dict[str, Any] = {
        "task_id": task_id,
        "filename": filename,
        "dry_run": dry_run,
    }
    if len(ri.edits) > 1:
        params["edits"] = [e.to_dict() for e in ri.edits if e.old_text or e.start_line is not None]
    elif len(ri.edits) == 1:
        edit = ri.edits[0]
        if edit.old_text:
            params["old_text"] = edit.old_text
        if edit.new_text:
            params["new_text"] = edit.new_text
        if edit.occurrence_index is not None:
            params["occurrence_index"] = edit.occurrence_index
        if edit.start_line is not None:
            params["start_line"] = edit.start_line
        if edit.end_line is not None:
            params["end_line"] = edit.end_line
        if edit.replace_all:
            params["replace_all"] = True
    return params


def revision_intent_to_read_params(
    revision_intent: dict[str, Any] | RevisionIntent,
    task_id: str,
    *,
    max_chars: int = 12000,
) -> dict[str, Any]:
    """Scoped read window from first edit line range or resolved sections."""
    ri = _coerce_intent(revision_intent)
    if ri is None:
        raise ValueError("invalid revision_intent")
    params: dict[str, Any] = {
        "task_id": task_id,
        "filename": _safe_filename(ri.artifact_filename),
        "max_chars": max_chars,
        "use_cache": False,
        "with_line_numbers": True,
    }
    for edit in ri.edits:
        if edit.start_line is not None:
            params["start_line"] = edit.start_line
            params["end_line"] = edit.end_line or edit.start_line
            break
    return params


def enrich_revision_edits_from_sections(
    revision_intent: RevisionIntent,
    content: str,
) -> RevisionIntent:
    """Resolve target_sections into line ranges when edits lack anchors."""
    from app.services.writing.section_locator import resolve_sections

    if not revision_intent.target_sections:
        return revision_intent
    if any(e.old_text or e.start_line is not None for e in revision_intent.edits):
        return revision_intent
    spans = resolve_sections(content, revision_intent.target_sections)
    if not spans:
        return revision_intent
    edits: list[RevisionEdit] = []
    for start_line, end_line, anchor_text in spans:
        edits.append(
            RevisionEdit(
                start_line=start_line,
                end_line=end_line,
                old_text=anchor_text or "",
            )
        )
    revision_intent.edits = edits
    return revision_intent
