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


def _safe_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    if not name or name in (".", ".."):
        raise ValueError("Invalid filename")
    suffix = Path(name).suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(_ALLOWED_EXTENSIONS))
        raise ValueError(f"Extension not allowed. Use one of: {allowed}")
    return name


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


def handle_write_text_artifact(params: dict[str, Any]) -> dict[str, Any]:
    task_id = str(params["task_id"])
    filename = _safe_filename(str(params["filename"]))
    content = str(params.get("content", ""))
    chunks = split_long_text(content)
    path = task_artifact_dir(task_id) / filename

    if not chunks:
        _check_size(content, existing_bytes=0)
        path.write_text(content, encoding="utf-8")
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
    return {
        "path": str(path),
        "filename": filename,
        "bytes": len(final_content.encode("utf-8")),
        "mode": "write",
        "chunks_written": len(chunks),
        "status": "ok",
    }


def handle_append_text_artifact(params: dict[str, Any]) -> dict[str, Any]:
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
    occurrences = target.count(old_text)
    if occurrences == 0:
        raise ValueError("old_text not found in selected scope")
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
    return updated, replacements, {
        "operation": operation,
        "scope": {
            "start_line": start_line,
            "end_line": end_line,
        },
        "occurrence_index": occurrence_index,
        "scope_match_count": occurrences,
    }



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
    path = task_artifact_dir(task_id) / filename
    if not path.exists():
        raise FileNotFoundError(f"Artifact not found: {filename}")
    original = path.read_text(encoding="utf-8")
    if not old_text:
        raise ValueError("old_text is required")
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
    return {
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


def handle_read_text_artifact(params: dict[str, Any]) -> dict[str, Any]:
    task_id = str(params["task_id"])
    filename = _safe_filename(str(params["filename"]))
    max_chars = int(params.get("max_chars", 8000))
    path = task_artifact_dir(task_id) / filename
    if not path.exists():
        raise FileNotFoundError(f"Artifact not found: {filename}")
    text = path.read_text(encoding="utf-8")
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    return {
        "path": str(path),
        "filename": filename,
        "content": text,
        "total_chars": len(path.read_text(encoding="utf-8")),
        "tail_excerpt": read_artifact_tail(task_id, filename, max_chars=min(1200, max_chars)),
        "truncated": truncated,
        "status": "ok",
    }


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
