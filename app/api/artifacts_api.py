from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from fastapi.responses import FileResponse, PlainTextResponse

from app.api.deps import get_current_principal
from app.services.artifact_tools import list_task_artifacts, task_artifact_dir
from app.services.auth_service import AuthPrincipal

router = APIRouter(prefix="/tasks", tags=["artifacts"])


class FileWritePayload(BaseModel):
    path: str
    content: str
    append: bool = False


def _resolve_in_task_dir(task_id: str, raw_path: str) -> Path:
    root = task_artifact_dir(task_id).resolve()
    rel = Path(str(raw_path).strip())
    if rel.is_absolute():
        raise ValueError("absolute path is not allowed")
    target = (root / rel).resolve()
    if not target.is_relative_to(root):
        raise ValueError("path escapes task directory")
    return target


@router.get("/{task_id}/artifacts")
def list_artifacts(
    task_id: str,
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict:
    """List files written for a task."""
    try:
        items = list_task_artifacts(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"task_id": task_id, "artifacts": items, "total": len(items)}


@router.get("/{task_id}/artifacts/{filename}")
def download_artifact(
    task_id: str,
    filename: str,
    _principal: AuthPrincipal = Depends(get_current_principal),
):
    """Download a task artifact file."""
    try:
        path = task_artifact_dir(task_id) / Path(filename).name
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"Artifact not found: {filename}")
    return FileResponse(path, filename=path.name, media_type="text/plain; charset=utf-8")


@router.get("/{task_id}/files")
def list_task_files(
    task_id: str,
    path: str = Query(".", description="relative path under task artifact directory"),
    recursive: bool = Query(False),
    max_entries: int = Query(300, ge=1, le=2000),
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    try:
        base = _resolve_in_task_dir(task_id, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not base.exists() or not base.is_dir():
        raise HTTPException(status_code=404, detail=f"Directory not found: {path}")

    out: list[dict[str, Any]] = []
    iterator = base.rglob("*") if recursive else base.iterdir()
    for entry in iterator:
        rel = entry.relative_to(base)
        out.append(
            {
                "path": str(rel),
                "type": "dir" if entry.is_dir() else "file",
                "size": entry.stat().st_size if entry.is_file() else None,
                "mtime_ms": int(entry.stat().st_mtime * 1000),
            }
        )
        if len(out) >= max_entries:
            break
    return {
        "task_id": task_id,
        "base_path": str(base),
        "entries": out,
        "count": len(out),
        "truncated": len(out) >= max_entries,
    }


@router.get("/{task_id}/files/content")
def read_task_file_content(
    task_id: str,
    path: str = Query(..., description="relative file path under task artifacts"),
    offset: int = Query(0, ge=0),
    max_chars: int = Query(12000, ge=1, le=300000),
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    try:
        file_path = _resolve_in_task_dir(task_id, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=415, detail=f"Not a UTF-8 text file: {path}") from exc
    sliced = content[offset : offset + max_chars]
    return {
        "task_id": task_id,
        "path": path,
        "content": sliced,
        "offset": offset,
        "returned_chars": len(sliced),
        "total_chars": len(content),
        "truncated": offset + max_chars < len(content),
        "mtime_ms": int(file_path.stat().st_mtime * 1000),
    }


@router.put("/{task_id}/files/content")
def write_task_file_content(
    task_id: str,
    payload: FileWritePayload,
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    try:
        file_path = _resolve_in_task_dir(task_id, payload.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    file_path.parent.mkdir(parents=True, exist_ok=True)
    if payload.append:
        with file_path.open("a", encoding="utf-8") as handle:
            handle.write(payload.content)
        mode = "append"
    else:
        file_path.write_text(payload.content, encoding="utf-8")
        mode = "write"
    content = file_path.read_text(encoding="utf-8")
    return {
        "task_id": task_id,
        "path": payload.path,
        "mode": mode,
        "bytes": len(content.encode("utf-8")),
        "total_chars": len(content),
        "mtime_ms": int(file_path.stat().st_mtime * 1000),
        "status": "ok",
    }
