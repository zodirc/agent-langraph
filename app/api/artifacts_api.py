from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

from app.api.deps import get_current_principal
from app.services.artifact_tools import list_task_artifacts, task_artifact_dir
from app.services.auth_service import AuthPrincipal

router = APIRouter(prefix="/tasks", tags=["artifacts"])


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
