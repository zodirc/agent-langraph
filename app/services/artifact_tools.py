from __future__ import annotations

import ast
import difflib
import logging
import operator
import re
import shutil
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

from app.config.settings import settings
from app.services.audit_store import get_audit_store

_ALLOWED_EXTENSIONS = frozenset({".md", ".txt", ".json", ".csv", ".log"})
_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def artifacts_root() -> Path:
    return Path(settings.ARTIFACTS_PATH)


def task_artifact_dir(task_id: str) -> Path:
    path = _task_artifact_path(task_id)
    if path is None:
        raise ValueError("Invalid task_id for artifact path")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _task_artifact_path(task_id: str) -> Path | None:
    safe_id = re.sub(r"[^a-zA-Z0-9\-_]", "", task_id)
    if not safe_id:
        return None
    return artifacts_root() / safe_id


def delete_task_artifact_dir(task_id: str) -> bool:
    """
    Remove on-disk session/task workspace (outline.txt, novel.txt, etc.).
    Best-effort; returns True if absent or successfully removed.
    """
    path = _task_artifact_path(task_id)
    if path is None:
        return False
    if not path.exists():
        return True
    if not path.is_dir():
        logger.warning("artifact path is not a directory: %s", path)
        return False
    root = artifacts_root().resolve()
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        logger.warning("refusing to delete artifact path outside root: %s", resolved)
        return False
    try:
        shutil.rmtree(resolved)
        return True
    except OSError as exc:
        logger.warning("failed to delete artifact dir %s: %s", resolved, exc)
        return False


def is_text_artifact_filename(filename: str) -> bool:
    """True when basename uses manuscript/text-artifact extensions (.md, .txt, …)."""
    name = Path(str(filename or "")).name.strip()
    if not name:
        return False
    return Path(name).suffix.lower() in _ALLOWED_EXTENSIONS


def _safe_filename(filename: str) -> str:
    """Return a safe artifact-relative path (supports one subdir, e.g. 正文/第001章.md)."""
    raw = str(filename or "").replace("\\", "/").strip().lstrip("/")
    if not raw:
        raise ValueError("Invalid filename")
    parts = [p for p in raw.split("/") if p and p not in (".", "..")]
    if not parts or any(part in (".", "..") for part in parts):
        raise ValueError("Invalid filename")
    if len(parts) > 2:
        raise ValueError("Artifact path too deep")
    name = parts[-1]
    suffix = Path(name).suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(_ALLOWED_EXTENSIONS))
        raise ValueError(f"Extension not allowed. Use one of: {allowed}")
    if len(parts) == 1:
        return name
    return f"{parts[0]}/{name}"


def _check_size(content: str, existing_bytes: int = 0) -> None:
    new_bytes = len(content.encode("utf-8"))
    if new_bytes > settings.ARTIFACT_MAX_WRITE_BYTES:
        raise ValueError(
            f"Content exceeds max write size ({settings.ARTIFACT_MAX_WRITE_BYTES} bytes)"
        )
    if existing_bytes + new_bytes > settings.ARTIFACT_MAX_FILE_BYTES:
        raise ValueError(
            f"File would exceed max size ({settings.ARTIFACT_MAX_FILE_BYTES} bytes)"
        )


def handle_get_runtime_info(params: dict[str, Any]) -> dict[str, Any]:
    """Return truthful runtime capabilities (avoids model identity hallucination)."""
    from app.services.runtime_limits import build_runtime_limits
    from app.services.tool_registry import get_tool_registry

    return {
        "model_name": settings.MODEL_NAME,
        "model_enabled": settings.MODEL_ENABLED,
        "model_provider": settings.MODEL_PROVIDER,
        "knowledge_backend": settings.KNOWLEDGE_BACKEND,
        "web_search": False,
        "internet_access": False,
        "knowledge_retrieval": True,
        "artifacts_path": str(artifacts_root()),
        "artifact_max_write_bytes": settings.ARTIFACT_MAX_WRITE_BYTES,
        "available_tools": get_tool_registry().list_tools(),
        **build_runtime_limits(),
        "note": (
            "Use local knowledge retrieval for stored documents, not live web search. "
            "Use write_text_artifact / append_text_artifact to save generated text to files."
        ),
    }


def _assert_write_allowed(params: dict[str, Any]) -> None:
    """Optional run fence when ``_agent_state`` is supplied by the executor."""
    state = params.get("_agent_state")
    if state is None:
        return
    from app.services.run_controller import RunCancelled, RunController

    if not RunController.should_accept_write(state):
        raise RunCancelled("artifact write rejected: run inactive or superseded")


_BODY_WRITE_OPS = frozenset({"append", "kickoff_body"})
_OUTLINE_TARGET_RE = re.compile(r"(?i)(大纲|outline)")


def _guard_body_write_target(params: dict[str, Any]) -> dict[str, Any] | None:
    """Reject body operators targeting outline files (hard safety rail)."""
    if params.get("_chapter_path_retry"):
        return None
    operator = str(params.get("writing_operator") or "")
    if operator not in _BODY_WRITE_OPS:
        return None
    filename = str(params.get("filename") or "")
    if _OUTLINE_TARGET_RE.search(filename):
        return {
            "status": "error",
            "error_code": "body_write_targets_outline",
            "message": f"正文写入目标不能是大纲文件: {filename}",
            "filename": filename,
        }
    return None


def _retry_body_write_with_chapter_path(
    params: dict[str, Any],
    *,
    write_fn,
) -> dict[str, Any]:
    """On outline-target guard failure, retry once with project chapter path."""
    task_id = str(params.get("task_id") or "")
    operator = str(params.get("writing_operator") or "")
    from app.services.writing_project import load_project, resolve_body_target

    if not task_id or not load_project(task_id):
        return write_fn(params)
    alt = resolve_body_target(task_id, "", operator=operator)
    if not alt or alt == params.get("filename"):
        return {
            "status": "error",
            "error_code": "body_write_targets_outline",
            "message": "正文写入目标不能是大纲文件",
            "filename": str(params.get("filename") or ""),
        }
    retry_params = {**params, "filename": alt, "_chapter_path_retry": True}
    return write_fn(retry_params)


def handle_write_text_artifact(params: dict[str, Any]) -> dict[str, Any]:
    _assert_write_allowed(params)
    guard = _guard_body_write_target(params)
    if guard:
        return _retry_body_write_with_chapter_path(params, write_fn=handle_write_text_artifact)
    task_id = str(params["task_id"])
    filename = _safe_filename(str(params["filename"]))
    content = str(params.get("content", ""))
    chunks = split_long_text(content)
    path = task_artifact_dir(task_id) / filename

    if not chunks:
        _check_size(content, existing_bytes=0)
        path.write_text(content, encoding="utf-8")
        from app.services.writing_project import post_chapter_write_update

        post_chapter_write_update(task_id, filename, char_count=len(content))
        return {
            "path": str(path),
            "filename": filename,
            "bytes": len(content.encode("utf-8")),
            "mode": "write",
            "chunks_written": 1,
            "status": "ok",
        }

    assembled_parts: list[str] = []
    existing_bytes = 0
    for chunk in chunks:
        _check_size(chunk, existing_bytes=existing_bytes)
        assembled_parts.append(chunk)
        existing_bytes += len(chunk.encode("utf-8"))
    final_content = "\n\n".join(part for part in assembled_parts if part)
    path.write_text(final_content, encoding="utf-8")
    from app.services.writing_project import post_chapter_write_update

    post_chapter_write_update(task_id, filename, char_count=len(final_content))
    return {
        "path": str(path),
        "filename": filename,
        "bytes": len(final_content.encode("utf-8")),
        "mode": "write",
        "chunks_written": len(chunks),
        "status": "ok",
    }


def handle_append_text_artifact(params: dict[str, Any]) -> dict[str, Any]:
    _assert_write_allowed(params)
    guard = _guard_body_write_target(params)
    if guard:
        return _retry_body_write_with_chapter_path(params, write_fn=handle_append_text_artifact)
    task_id = str(params["task_id"])
    filename = _safe_filename(str(params["filename"]))
    content = str(params.get("content", ""))
    path = task_artifact_dir(task_id) / filename
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    chunks = split_long_text(content)

    if not chunks:
        _check_size(content, existing_bytes=len(existing.encode("utf-8")))
        with path.open("a", encoding="utf-8") as handle:
            if existing.strip() and not existing.endswith("\n"):
                handle.write("\n\n")
            handle.write(content)
        total = path.read_text(encoding="utf-8")
        from app.services.writing_project import post_chapter_write_update

        post_chapter_write_update(task_id, filename, char_count=len(total))
        return {
            "path": str(path),
            "filename": filename,
            "appended_bytes": len(content.encode("utf-8")),
            "total_bytes": len(total.encode("utf-8")),
            "mode": "append",
            "chunks_written": 1,
            "status": "ok",
        }

    current_bytes = len(existing.encode("utf-8"))
    with path.open("a", encoding="utf-8") as handle:
        for index, chunk in enumerate(chunks):
            _check_size(chunk, existing_bytes=current_bytes)
            if (existing.strip() or index > 0) and not _ends_with_blank_separator(handle, path, existing, index):
                handle.write("\n\n")
                current_bytes += len("\n\n".encode("utf-8"))
            handle.write(chunk)
            current_bytes += len(chunk.encode("utf-8"))
    total = path.read_text(encoding="utf-8")
    from app.services.writing_project import post_chapter_write_update

    post_chapter_write_update(task_id, filename, char_count=len(total))
    return {
        "path": str(path),
        "filename": filename,
        "appended_bytes": len(content.encode("utf-8")),
        "total_bytes": len(total.encode("utf-8")),
        "mode": "append",
        "chunks_written": len(chunks),
        "status": "ok",
    }


def _ends_with_blank_separator(
    handle: Any,
    path: Path,
    existing: str,
    index: int,
) -> bool:
    if index > 0:
        return False
    if existing:
        return existing.endswith("\n\n")
    try:
        return path.read_text(encoding="utf-8").endswith("\n\n")
    except OSError:
        return False


def _build_diff_preview(filename: str, before: str, after: str) -> str:
    diff_lines = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"before/{filename}",
            tofile=f"after/{filename}",
            lineterm="",
        )
    )
    return "\n".join(diff_lines[:80])



def _audit_artifact_edit(
    task_id: str,
    filename: str,
    *,
    operation: str,
    before: str,
    after: str,
    user_role: str,
    dry_run: bool,
    selection: dict[str, Any] | None = None,
) -> None:
    get_audit_store().append_events(
        task_id,
        [
            {
                "type": "artifact_edit",
                "tool": "edit_text_artifact",
                "operation": operation,
                "filename": filename,
                "user_role": user_role,
                "dry_run": dry_run,
                "before_bytes": len(before.encode("utf-8")),
                "after_bytes": len(after.encode("utf-8")),
                "selection": selection or {},
                "diff_preview": _build_diff_preview(filename, before, after),
            }
        ],
    )



def _normalize_edit_text(text: str) -> str:
    """Fold whitespace and strip common streaming escape noise for fuzzy anchor match."""
    import unicodedata

    raw = str(text or "")
    raw = raw.replace("\\-", "-").replace("\\_", "_").replace("\\*", "*")
    raw = raw.replace("\\`", "`").replace("\\.", ".")
    raw = re.sub(r"\\([^\s])", r"\1", raw)
    raw = unicodedata.normalize("NFKC", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def _find_fuzzy_span(target: str, old_text: str) -> tuple[int, int] | None:
    """Locate old_text in target using normalized comparison; return char span in target."""
    if not old_text:
        return None
    if old_text in target:
        start = target.index(old_text)
        return start, start + len(old_text)

    norm_old = _normalize_edit_text(old_text)
    if not norm_old:
        return None

    lines = target.splitlines(keepends=True)
    if not lines:
        return None

    norm_lines = [_normalize_edit_text(line) for line in lines]
    joined = "".join(norm_lines)
    pos = joined.find(norm_old)
    if pos >= 0:
        cursor = 0
        start_idx = 0
        end_idx = len(target)
        norm_cursor = 0
        for line, norm_line in zip(lines, norm_lines):
            line_start = cursor
            line_norm_start = norm_cursor
            line_norm_end = norm_cursor + len(norm_line)
            if line_norm_start <= pos < line_norm_end:
                start_idx = line_start + max(0, pos - line_norm_start)
            if line_norm_start < pos + len(norm_old) <= line_norm_end:
                end_idx = line_start + min(len(line), pos + len(norm_old) - line_norm_start)
                break
            cursor += len(line)
            norm_cursor = line_norm_end
        if end_idx > start_idx:
            return start_idx, end_idx

    best: tuple[float, int, int] | None = None
    window = max(len(norm_old), 8)
    step = max(1, window // 4)
    for start in range(0, max(1, len(joined) - window + 1), step):
        chunk = joined[start : start + window + len(norm_old)]
        ratio = difflib.SequenceMatcher(None, norm_old, chunk[: len(norm_old) + window]).ratio()
        if ratio >= 0.82 and (best is None or ratio > best[0]):
            best = (ratio, start, start + len(norm_old))
    if best is None:
        return None
    _, norm_start, norm_end = best
    cursor = 0
    norm_cursor = 0
    start_idx = 0
    end_idx = len(target)
    for line, norm_line in zip(lines, norm_lines):
        line_start = cursor
        line_norm_start = norm_cursor
        line_norm_end = norm_cursor + len(norm_line)
        if line_norm_start <= norm_start < line_norm_end:
            start_idx = line_start
        if line_norm_start < norm_end <= line_norm_end:
            end_idx = line_start + len(line)
            break
        cursor += len(line)
        norm_cursor = line_norm_end
    if end_idx <= start_idx:
        return None
    return start_idx, end_idx


def _nearest_candidate(target: str, old_text: str, *, limit: int = 240) -> str:
    norm_old = _normalize_edit_text(old_text)
    if not norm_old:
        return ""
    lines = target.splitlines()
    best_line = ""
    best_ratio = 0.0
    for line in lines:
        ratio = difflib.SequenceMatcher(None, norm_old, _normalize_edit_text(line)).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_line = line
    if best_line:
        return best_line[:limit]
    return target[:limit]


def _slice_bounds(content: str, start_line: int | None, end_line: int | None) -> tuple[int, int]:
    if start_line is None and end_line is None:
        return 0, len(content)
    lines = content.splitlines(keepends=True)
    if not lines:
        return 0, 0
    resolved_start = 1 if start_line is None else start_line
    resolved_end = len(lines) if end_line is None else end_line
    if resolved_start < 1 or resolved_end < resolved_start or resolved_end > len(lines):
        raise ValueError("Invalid line range")
    start_idx = sum(len(line) for line in lines[: resolved_start - 1])
    end_idx = sum(len(line) for line in lines[:resolved_end])
    return start_idx, end_idx



def _apply_precision_edit(
    original: str,
    *,
    old_text: str,
    new_text: str,
    replace_all: bool,
    occurrence_index: int | None,
    start_line: int | None,
    end_line: int | None,
) -> tuple[str, int, dict[str, Any]]:
    start_idx, end_idx = _slice_bounds(original, start_line, end_line)
    target = original[start_idx:end_idx]
    match_mode = "exact"
    resolved_old = old_text
    occurrences = target.count(old_text)
    if occurrences == 0:
        span = _find_fuzzy_span(target, old_text)
        if span is not None:
            match_mode = "fuzzy"
            rel_start, rel_end = span
            resolved_old = target[rel_start:rel_end]
            occurrences = 1
            if occurrence_index is not None or replace_all:
                old_text = resolved_old
            else:
                updated = (
                    original[: start_idx + rel_start]
                    + new_text
                    + original[start_idx + rel_end :]
                )
                return updated, 1, {
                    "operation": "replace_fuzzy",
                    "scope": {"start_line": start_line, "end_line": end_line},
                    "occurrence_index": occurrence_index,
                    "scope_match_count": 1,
                    "match_mode": match_mode,
                    "resolved_old_text": resolved_old,
                }
        else:
            candidate = _nearest_candidate(target, old_text)
            hint = f" (nearest: {candidate[:120]!r})" if candidate else ""
            raise ValueError(f"old_text not found in selected scope{hint}")
    old_text = resolved_old if match_mode == "fuzzy" else old_text
    if occurrence_index is not None:
        if occurrence_index < 1 or occurrence_index > occurrences:
            raise ValueError("occurrence_index out of range")
        cursor = 0
        replace_start = -1
        for _ in range(occurrence_index):
            replace_start = target.find(old_text, cursor)
            cursor = replace_start + len(old_text)
        assert replace_start >= 0
        replace_end = replace_start + len(old_text)
        replaced_target = target[:replace_start] + new_text + target[replace_end:]
        replacements = 1
        operation = "replace_occurrence"
    else:
        if occurrences > 1 and not replace_all:
            raise ValueError("old_text matched multiple locations; set replace_all=true or provide occurrence_index")
        replaced_target = target.replace(old_text, new_text) if replace_all else target.replace(old_text, new_text, 1)
        replacements = occurrences if replace_all else 1
        operation = "replace_all" if replace_all else "replace_one"
    updated = original[:start_idx] + replaced_target + original[end_idx:]
    selection: dict[str, Any] = {
        "operation": operation,
        "scope": {
            "start_line": start_line,
            "end_line": end_line,
        },
        "occurrence_index": occurrence_index,
        "scope_match_count": occurrences,
    }
    if match_mode != "exact":
        selection["match_mode"] = match_mode
        selection["resolved_old_text"] = old_text
    return updated, replacements, selection


def _apply_batch_edits(
    original: str,
    edits: list[dict[str, Any]],
) -> tuple[str, int, list[dict[str, Any]]]:
    updated = original
    total = 0
    details: list[dict[str, Any]] = []
    for idx, edit in enumerate(edits):
        if not isinstance(edit, dict):
            continue
        old_text = str(edit.get("old_text") or "")
        if not old_text:
            continue
        updated, replacements, selection = _apply_precision_edit(
            updated,
            old_text=old_text,
            new_text=str(edit.get("new_text") or ""),
            replace_all=bool(edit.get("replace_all", False)),
            occurrence_index=(
                int(edit["occurrence_index"]) if edit.get("occurrence_index") is not None else None
            ),
            start_line=int(edit["start_line"]) if edit.get("start_line") is not None else None,
            end_line=int(edit["end_line"]) if edit.get("end_line") is not None else None,
        )
        total += replacements
        details.append({"index": idx, "replacements": replacements, "selection": selection})
    return updated, total, details



def handle_edit_text_artifact(params: dict[str, Any]) -> dict[str, Any]:
    task_id = str(params["task_id"])
    filename = _safe_filename(str(params["filename"]))
    old_text = str(params.get("old_text", ""))
    new_text = str(params.get("new_text", ""))
    replace_all = bool(params.get("replace_all", False))
    user_role = str(params.get("user_role", "user"))
    occurrence_index = params.get("occurrence_index")
    occurrence_index = int(occurrence_index) if occurrence_index is not None else None
    start_line = params.get("start_line")
    start_line = int(start_line) if start_line is not None else None
    end_line = params.get("end_line")
    end_line = int(end_line) if end_line is not None else None
    dry_run = bool(params.get("dry_run", False))
    batch_edits = params.get("edits")
    path = task_artifact_dir(task_id) / filename
    if not path.exists():
        raise FileNotFoundError(f"Artifact not found: {filename}")
    original = path.read_text(encoding="utf-8")
    batch_details: list[dict[str, Any]] = []
    if isinstance(batch_edits, list) and batch_edits:
        updated, replacements, batch_details = _apply_batch_edits(original, batch_edits)
        selection = {"operation": "batch_replace", "batch_count": len(batch_details)}
    else:
        if not old_text and start_line is None and end_line is None:
            raise ValueError("old_text is required unless edits[] or line range is provided")
        if not old_text and start_line is not None:
            old_text = "\n".join(
                original.splitlines()[start_line - 1 : (end_line or start_line)]
            )
        updated, replacements, selection = _apply_precision_edit(
            original,
            old_text=old_text,
            new_text=new_text,
            replace_all=replace_all,
            occurrence_index=occurrence_index,
            start_line=start_line,
            end_line=end_line,
        )
    _check_size(updated, existing_bytes=0)
    if not dry_run:
        path.write_text(updated, encoding="utf-8")
    _audit_artifact_edit(
        task_id,
        filename,
        operation=selection["operation"],
        before=original,
        after=updated,
        user_role=user_role,
        dry_run=dry_run,
        selection=selection,
    )
    result = {
        "path": str(path),
        "filename": filename,
        "replacements": replacements,
        "bytes": len(updated.encode("utf-8")),
        "mode": "edit",
        "dry_run": dry_run,
        "selection": selection,
        "diff_preview": _build_diff_preview(filename, original, updated),
        "status": "ok",
    }
    if batch_details:
        result["batch_details"] = batch_details
    _emit_edit_diff_stream(task_id, filename, result.get("diff_preview") or "")
    return result


def _emit_edit_diff_stream(task_id: str, filename: str, diff_preview: str) -> None:
    if not diff_preview:
        return
    try:
        from app.services.reasoning_trace import report_block, trace_enabled

        if trace_enabled():
            report_block(
                "writing",
                "diff",
                f"【编辑差异】{filename}\n{diff_preview[:2000]}",
                field="diff_preview",
            )
    except Exception:
        logger.debug("diff_preview stream skipped", exc_info=True)


def _format_with_line_numbers(text: str) -> str:
    lines = text.splitlines()
    width = max(4, len(str(len(lines) or 1)))
    return "\n".join(f"{idx:>{width}}| {line}" for idx, line in enumerate(lines, start=1))


def handle_read_text_artifact(params: dict[str, Any]) -> dict[str, Any]:
    task_id = str(params["task_id"])
    filename = _safe_filename(str(params["filename"]))
    max_chars = int(params.get("max_chars", 8000))
    with_line_numbers = bool(params.get("with_line_numbers", False))
    use_cache = params.get("use_cache", True) is not False
    start_line_raw = params.get("start_line")
    end_line_raw = params.get("end_line")
    start_line = int(start_line_raw) if start_line_raw is not None else None
    end_line = int(end_line_raw) if end_line_raw is not None else None
    scoped = start_line is not None or end_line is not None
    path = task_artifact_dir(task_id) / filename
    if not path.exists():
        raise FileNotFoundError(f"Artifact not found: {filename}")

    if use_cache and not with_line_numbers and not scoped:
        from app.services.artifact_read_cache import get_cached_read, store_cached_read

        cached = get_cached_read(task_id, filename, path)
        if cached is not None:
            return dict(cached)

    full_text = path.read_text(encoding="utf-8")
    scope_start = start_line
    scope_end = end_line
    if scoped:
        start_idx, end_idx = _slice_bounds(full_text, start_line, end_line)
        full_text = full_text[start_idx:end_idx]
    truncated = len(full_text) > max_chars
    text = full_text[:max_chars] if truncated else full_text
    display = _format_with_line_numbers(text) if with_line_numbers else text
    result = {
        "path": str(path),
        "filename": filename,
        "content": display,
        "raw_content": text,
        "total_chars": len(full_text),
        "line_count": len(full_text.splitlines()),
        "tail_excerpt": read_artifact_tail(task_id, filename, max_chars=min(1200, max_chars)),
        "truncated": truncated,
        "with_line_numbers": with_line_numbers,
        "status": "ok",
    }
    if scoped:
        result["scope"] = {"start_line": scope_start, "end_line": scope_end}
    if use_cache and not with_line_numbers and not scoped:
        from app.services.artifact_read_cache import store_cached_read

        store_cached_read(task_id, filename, path, result)
    return result


def read_artifact_tail(task_id: str, filename: str, max_chars: int = 1200) -> str:
    path = task_artifact_dir(task_id) / _safe_filename(filename)
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8")
    return content[-max_chars:]


def split_long_text(text: str) -> list[str]:
    content = str(text)
    if not content:
        return []
    chunk_chars = max(1, int(settings.ARTIFACT_CHUNK_CHARS))
    if len(content) <= chunk_chars:
        return [content]

    chunks: list[str] = []
    cursor = 0
    while cursor < len(content):
        end = min(len(content), cursor + chunk_chars)
        window = content[cursor:end]
        if end < len(content):
            last_break = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind("。"), window.rfind(" "))
            if last_break >= max(200, chunk_chars // 3):
                end = cursor + last_break + 1
                window = content[cursor:end]
        cleaned = window.strip()
        if cleaned:
            chunks.append(cleaned)
        cursor = end
    return chunks or [content]


def _eval_ast(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Only numeric constants allowed")
    if isinstance(node, ast.BinOp):
        op = _OPS.get(type(node.op))
        if not op:
            raise ValueError("Unsupported operator")
        return op(_eval_ast(node.left), _eval_ast(node.right))
    if isinstance(node, ast.UnaryOp):
        op = _OPS.get(type(node.op))
        if not op:
            raise ValueError("Unsupported unary operator")
        return op(_eval_ast(node.operand))
    raise ValueError("Unsupported expression")


def handle_calculator(params: dict[str, Any]) -> dict[str, Any]:
    expression = str(params.get("expression", "")).strip()
    if not expression:
        raise ValueError("expression is required")
    if len(expression) > 500:
        raise ValueError("expression too long")
    tree = ast.parse(expression, mode="eval")
    value = _eval_ast(tree.body)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return {
        "expression": expression,
        "result": str(value),
        "status": "ok",
    }


def extract_math_expression(goal: str) -> Optional[str]:
    """Pull a simple arithmetic expression from user text."""
    text = goal.strip()
    if not text:
        return None
    for suffix in ("是多少", "等于多少", "=?", "？"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    if re.match(r"^[\d\s+\-*/().]+$", text):
        return text
    match = re.search(r"([\d\s+\-*/().]{3,})", text)
    if match:
        candidate = match.group(1).strip()
        if any(op in candidate for op in "+-*/") and re.match(r"^[\d\s+\-*/().]+$", candidate):
            return candidate
    return None


def collect_file_artifacts(tool_results: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Extract file artifact metadata from tool results."""
    artifacts: list[dict[str, Any]] = []
    for item in tool_results or []:
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        if not isinstance(result, dict):
            continue
        if result.get("path") and result.get("filename"):
            artifacts.append(
                {
                    "type": "file",
                    "filename": result["filename"],
                    "path": result["path"],
                    "bytes": result.get("bytes") or result.get("total_bytes"),
                    "mode": result.get("mode"),
                    "chunks_written": result.get("chunks_written", 1),
                }
            )
    return artifacts


def list_task_artifacts(task_id: str) -> list[dict[str, Any]]:
    directory = task_artifact_dir(task_id)
    if not directory.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(directory.iterdir()):
        if path.is_file():
            items.append(
                {
                    "filename": path.name,
                    "path": str(path),
                    "bytes": path.stat().st_size,
                }
            )
    return items
