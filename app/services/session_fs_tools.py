from __future__ import annotations

import difflib
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from app.services.artifact_tools import task_artifact_dir

_RM_PREVIEW_TTL_SECONDS = 600
_RM_PREVIEW_TOKENS: dict[str, dict[str, Any]] = {}


def _session_root(task_id: str) -> Path:
    return task_artifact_dir(task_id).resolve()


def _resolve_in_session(task_id: str, raw_path: str) -> Path:
    root = _session_root(task_id)
    rel = Path(str(raw_path).strip())
    if not str(rel):
        raise ValueError("path is required")
    if rel in (Path("."), Path("./")):
        raise ValueError("operation on session root is not allowed")
    if rel.is_absolute():
        raise ValueError("absolute path is not allowed")
    target = (root / rel).resolve()
    if not target.is_relative_to(root):
        raise ValueError("path escapes session directory")
    return target


def _resolve_in_session_allow_root(task_id: str, raw_path: str | None = None) -> Path:
    root = _session_root(task_id)
    raw = "." if raw_path is None else str(raw_path).strip() or "."
    rel = Path(raw)
    if rel.is_absolute():
        raise ValueError("absolute path is not allowed")
    target = (root / rel).resolve()
    if not target.is_relative_to(root):
        raise ValueError("path escapes session directory")
    return target


def handle_ls_path(params: dict[str, Any]) -> dict[str, Any]:
    task_id = str(params["task_id"])
    path = _resolve_in_session_allow_root(task_id, params.get("path"))
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f"Directory not found: {params.get('path', '.')}")
    recursive = bool(params.get("recursive", False))
    max_entries = max(1, int(params.get("max_entries", 200)))
    entries: list[dict[str, Any]] = []
    base = path
    iterator = path.rglob("*") if recursive else path.iterdir()
    for item in iterator:
        rel = item.relative_to(base)
        entries.append(
            {
                "path": str(rel),
                "type": "dir" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else None,
            }
        )
        if len(entries) >= max_entries:
            break
    return {
        "path": str(path),
        "entries": entries,
        "count": len(entries),
        "truncated": len(entries) >= max_entries,
        "status": "ok",
    }


def handle_read_file(params: dict[str, Any]) -> dict[str, Any]:
    task_id = str(params["task_id"])
    path = _resolve_in_session(task_id, str(params["path"]))
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"File not found: {params['path']}")
    offset = max(0, int(params.get("offset", 0)))
    max_chars = max(1, int(params.get("max_chars", 8000)))
    content = path.read_text(encoding="utf-8")
    sliced = content[offset : offset + max_chars]
    return {
        "path": str(path),
        "content": sliced,
        "offset": offset,
        "returned_chars": len(sliced),
        "total_chars": len(content),
        "truncated": offset + max_chars < len(content),
        "status": "ok",
    }


def handle_write_file(params: dict[str, Any]) -> dict[str, Any]:
    task_id = str(params["task_id"])
    path = _resolve_in_session(task_id, str(params["path"]))
    parents = bool(params.get("parents", False))
    content = str(params.get("content", ""))
    if parents:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.parent.exists():
        raise FileNotFoundError("parent directory does not exist")
    path.write_text(content, encoding="utf-8")
    return {
        "path": str(path),
        "bytes": len(content.encode("utf-8")),
        "mode": "write",
        "status": "ok",
    }


def handle_append_file(params: dict[str, Any]) -> dict[str, Any]:
    task_id = str(params["task_id"])
    path = _resolve_in_session(task_id, str(params["path"]))
    parents = bool(params.get("parents", False))
    content = str(params.get("content", ""))
    if parents:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.parent.exists():
        raise FileNotFoundError("parent directory does not exist")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(content)
    return {
        "path": str(path),
        "appended_bytes": len(content.encode("utf-8")),
        "mode": "append",
        "status": "ok",
    }


def handle_move_path(params: dict[str, Any]) -> dict[str, Any]:
    task_id = str(params["task_id"])
    src = _resolve_in_session(task_id, str(params["src"]))
    dst = _resolve_in_session(task_id, str(params["dst"]))
    parents = bool(params.get("parents", False))
    if not src.exists():
        raise FileNotFoundError(f"Path not found: {params['src']}")
    if parents:
        dst.parent.mkdir(parents=True, exist_ok=True)
    elif not dst.parent.exists():
        raise FileNotFoundError("destination parent directory does not exist")
    src.rename(dst)
    return {"src": str(src), "dst": str(dst), "status": "ok"}


def handle_copy_path(params: dict[str, Any]) -> dict[str, Any]:
    task_id = str(params["task_id"])
    src = _resolve_in_session(task_id, str(params["src"]))
    dst = _resolve_in_session(task_id, str(params["dst"]))
    recursive = bool(params.get("recursive", False))
    parents = bool(params.get("parents", False))
    if not src.exists():
        raise FileNotFoundError(f"Path not found: {params['src']}")
    if parents:
        dst.parent.mkdir(parents=True, exist_ok=True)
    elif not dst.parent.exists():
        raise FileNotFoundError("destination parent directory does not exist")
    if src.is_dir():
        if not recursive:
            raise ValueError("source is directory; set recursive=true")
        shutil.copytree(src, dst, dirs_exist_ok=False)
        copied_type = "dir"
    else:
        shutil.copy2(src, dst)
        copied_type = "file"
    return {"src": str(src), "dst": str(dst), "copied_type": copied_type, "status": "ok"}


def handle_grep_file(params: dict[str, Any]) -> dict[str, Any]:
    task_id = str(params["task_id"])
    path = _resolve_in_session(task_id, str(params["path"]))
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"File not found: {params['path']}")
    pattern = str(params.get("pattern", ""))
    if not pattern:
        raise ValueError("pattern is required")
    ignore_case = bool(params.get("ignore_case", False))
    use_regex = bool(params.get("regex", False))
    max_lines = int(params.get("max_lines", 200))
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    if use_regex:
        flags = re.IGNORECASE if ignore_case else 0
        compiled = re.compile(pattern, flags)
        matched = [line for line in lines if compiled.search(line)]
    else:
        needle = pattern.lower() if ignore_case else pattern
        matched = [
            line
            for line in lines
            if (line.lower() if ignore_case else line).find(needle) >= 0
        ]

    clipped = matched[:max(1, max_lines)]
    return {
        "path": str(path),
        "matched_lines": clipped,
        "matched_count": len(matched),
        "truncated": len(matched) > len(clipped),
        "status": "ok",
    }


def handle_replace_in_file(params: dict[str, Any]) -> dict[str, Any]:
    task_id = str(params["task_id"])
    path = _resolve_in_session(task_id, str(params["path"]))
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"File not found: {params['path']}")
    old_text = str(params.get("old_text", ""))
    new_text = str(params.get("new_text", ""))
    if not old_text:
        raise ValueError("old_text is required")
    replace_all = bool(params.get("replace_all", False))
    dry_run = bool(params.get("dry_run", False))
    use_regex = bool(params.get("regex", False))
    ignore_case = bool(params.get("ignore_case", False))
    content = path.read_text(encoding="utf-8")

    if use_regex:
        flags = re.IGNORECASE if ignore_case else 0
        compiled = re.compile(old_text, flags)
        count = 0 if replace_all else 1
        replaced, replacements = compiled.subn(new_text, content, count=count)
    else:
        if replace_all:
            replaced = content.replace(old_text, new_text)
            replacements = content.count(old_text)
        else:
            replaced = content.replace(old_text, new_text, 1)
            replacements = 1 if old_text in content else 0

    if replacements == 0:
        raise ValueError("old_text not found")
    diff_preview = "\n".join(
        difflib.unified_diff(
            content.splitlines(),
            replaced.splitlines(),
            fromfile=f"before/{path.name}",
            tofile=f"after/{path.name}",
            lineterm="",
        )
    )
    preview = "\n".join(diff_preview.splitlines()[:80])
    if not dry_run:
        path.write_text(replaced, encoding="utf-8")
    return {
        "path": str(path),
        "replacements": replacements,
        "dry_run": dry_run,
        "regex": use_regex,
        "ignore_case": ignore_case,
        "preview": preview,
        "status": "ok",
    }


def handle_touch_file(params: dict[str, Any]) -> dict[str, Any]:
    task_id = str(params["task_id"])
    path = _resolve_in_session(task_id, str(params["path"]))
    parents = bool(params.get("parents", False))
    if parents:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.parent.exists():
        raise FileNotFoundError("parent directory does not exist")
    existed = path.exists()
    path.touch(exist_ok=True)
    return {"path": str(path), "existed": existed, "status": "ok"}


def handle_mkdir_path(params: dict[str, Any]) -> dict[str, Any]:
    task_id = str(params["task_id"])
    path = _resolve_in_session(task_id, str(params["path"]))
    parents = bool(params.get("parents", True))
    exist_ok = bool(params.get("exist_ok", True))
    path.mkdir(parents=parents, exist_ok=exist_ok)
    return {"path": str(path), "status": "ok"}


def handle_rm_path(params: dict[str, Any]) -> dict[str, Any]:
    task_id = str(params["task_id"])
    root = _session_root(task_id)
    path = _resolve_in_session(task_id, str(params["path"]))
    recursive = bool(params.get("recursive", False))
    dry_run = bool(params.get("dry_run", False))
    preview_token = str(params.get("preview_token", "")).strip()

    if path == root:
        raise ValueError("cannot remove session root")
    if not path.exists():
        raise FileNotFoundError(f"Path not found: {params['path']}")

    if path.is_file():
        size = path.stat().st_size
        if dry_run:
            token = _issue_rm_preview_token(task_id, str(path), recursive)
            return {
                "path": str(path),
                "removed_type": "file",
                "bytes": size,
                "dry_run": True,
                "preview_token": token,
                "status": "ok",
            }
        _require_rm_preview_token(task_id, str(path), recursive, preview_token)
        if not dry_run:
            path.unlink()
        return {
            "path": str(path),
            "removed_type": "file",
            "bytes": size,
            "dry_run": dry_run,
            "status": "ok",
        }

    if not path.is_dir():
        raise ValueError("unsupported path type")

    child_count = sum(1 for _ in path.iterdir())
    if child_count > 0 and not recursive:
        raise ValueError("directory is not empty; set recursive=true")
    if dry_run:
        token = _issue_rm_preview_token(task_id, str(path), recursive)
        return {
            "path": str(path),
            "removed_type": "dir_recursive" if recursive else "dir",
            "entries": child_count,
            "dry_run": True,
            "preview_token": token,
            "status": "ok",
        }
    _require_rm_preview_token(task_id, str(path), recursive, preview_token)
    if not dry_run:
        if recursive:
            for child in sorted(path.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
            path.rmdir()
        else:
            path.rmdir()
    return {
        "path": str(path),
        "removed_type": "dir_recursive" if recursive else "dir",
        "entries": child_count,
        "dry_run": dry_run,
        "status": "ok",
    }


def _rm_preview_key(task_id: str, path: str, recursive: bool) -> str:
    return f"{task_id}|{path}|{int(recursive)}"


def _issue_rm_preview_token(task_id: str, path: str, recursive: bool) -> str:
    _cleanup_rm_preview_tokens()
    token = uuid.uuid4().hex
    key = _rm_preview_key(task_id, path, recursive)
    _RM_PREVIEW_TOKENS[key] = {"token": token, "at": time.time()}
    return token


def _require_rm_preview_token(task_id: str, path: str, recursive: bool, token: str) -> None:
    _cleanup_rm_preview_tokens()
    key = _rm_preview_key(task_id, path, recursive)
    row = _RM_PREVIEW_TOKENS.get(key)
    if not row or str(row.get("token")) != token:
        raise ValueError("preview_token required: run dry_run first")
    _RM_PREVIEW_TOKENS.pop(key, None)


def _cleanup_rm_preview_tokens() -> None:
    now = time.time()
    stale = [
        key
        for key, row in _RM_PREVIEW_TOKENS.items()
        if now - float(row.get("at") or 0) > _RM_PREVIEW_TTL_SECONDS
    ]
    for key in stale:
        _RM_PREVIEW_TOKENS.pop(key, None)
