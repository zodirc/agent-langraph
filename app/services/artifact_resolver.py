"""Generic task artifact target resolution — disk truth only (unified-core WP-6).

The planner specifies artifact filenames explicitly in Action params; this
module validates the requested name and provides a deterministic fallback when
a turn references "the artifact" without naming it (single file on disk).
All manuscript/outline role-pointer logic was removed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.services.artifact_tools import list_task_artifacts, task_artifact_dir


@dataclass(frozen=True)
class ArtifactEntry:
    filename: str
    bytes: int
    exists: bool

    def to_dict(self) -> dict[str, Any]:
        return {"filename": self.filename, "bytes": self.bytes, "exists": self.exists}


@dataclass(frozen=True)
class ResolvedTarget:
    filename: str
    source: str


class ArtifactResolutionError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        manifest: list[ArtifactEntry],
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.manifest = manifest

    def manifest_dicts(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.manifest]


def sanitize_artifact_basename(raw: Any) -> str:
    """Validate a single-segment artifact basename for task-local access."""
    from app.services.artifact_tools import _safe_filename

    name = str(raw or "").strip()
    if not name:
        raise ValueError("Artifact filename cannot be empty")
    return _safe_filename(name)


def _task_id(state: dict[str, Any]) -> str:
    payload = state.get("input_payload") if isinstance(state.get("input_payload"), dict) else {}
    return str(state.get("task_id") or (payload or {}).get("task_id") or "")


def build_artifact_manifest(task_id: str) -> list[ArtifactEntry]:
    entries: list[ArtifactEntry] = []
    for row in list_task_artifacts(task_id):
        if not isinstance(row, dict):
            continue
        name = str(row.get("filename") or "")
        if not name:
            continue
        entries.append(
            ArtifactEntry(
                filename=name,
                bytes=int(row.get("bytes") or 0),
                exists=True,
            )
        )
    return entries


def manifest_for_planning(state: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Planning input: current on-disk artifact manifest."""
    task_id = _task_id(state) or str(payload.get("task_id") or "")
    entries = build_artifact_manifest(task_id)
    return {"artifact_manifest": [e.to_dict() for e in entries]}


def _file_exists(task_id: str, filename: str) -> bool:
    if not filename:
        return False
    path = task_artifact_dir(task_id) / filename
    return path.exists()


def resolve_artifact_target(
    state: dict[str, Any],
    *,
    action: str = "",
    requested_filename: str = "",
    target_hint: str = "",
    require_exists: bool = True,
) -> ResolvedTarget:
    """Resolve the artifact a tool call should touch.

    Explicit filename wins. Without one, a single on-disk artifact is the
    unambiguous fallback for reads/edits; anything else is an error the
    planner must fix by naming the file.
    """
    task_id = _task_id(state)
    manifest = build_artifact_manifest(task_id)

    requested = str(requested_filename or "").strip()
    if requested:
        try:
            requested = sanitize_artifact_basename(requested)
        except ValueError as exc:
            raise ArtifactResolutionError(
                code="not_found", message=str(exc), manifest=manifest
            ) from exc
        if require_exists and not _file_exists(task_id, requested):
            raise ArtifactResolutionError(
                code="not_found",
                message=f"Artifact not found: {requested}",
                manifest=manifest,
            )
        return ResolvedTarget(filename=requested, source="requested")

    if len(manifest) == 1:
        return ResolvedTarget(filename=manifest[0].filename, source="disk")

    if manifest:
        raise ArtifactResolutionError(
            code="ambiguous",
            message=(
                "Multiple artifacts exist; specify filename explicitly: "
                + ", ".join(e.filename for e in manifest[:8])
            ),
            manifest=manifest,
        )
    raise ArtifactResolutionError(
        code="not_found",
        message=f"未找到产物文件（action={action or 'read'}）",
        manifest=manifest,
    )


def action_for_tool(tool_name: str, state: dict[str, Any]) -> str:
    """Generic action label for audit/error messages."""
    if tool_name == "read_text_artifact":
        return "read"
    if tool_name == "edit_text_artifact":
        return "edit"
    if tool_name in ("write_text_artifact", "append_text_artifact"):
        return "write"
    return tool_name


__all__ = [
    "ArtifactEntry",
    "ArtifactResolutionError",
    "ResolvedTarget",
    "action_for_tool",
    "build_artifact_manifest",
    "manifest_for_planning",
    "resolve_artifact_target",
    "sanitize_artifact_basename",
]
