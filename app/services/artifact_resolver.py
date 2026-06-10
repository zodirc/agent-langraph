"""Single authority for task artifact target resolution (role pointers + disk truth)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from app.config.settings import settings
from app.services.artifact_tools import list_task_artifacts
from app.services.manuscript_service import (
    Manuscript,
    _default_body,
    _default_outline,
    _is_outline_name,
    _pick_artifact_basename,
    artifact_bytes_on_disk,
    is_manuscript_pointer_filename,
    resolve_manuscript,
    sanitize_artifact_basename,
)

_OUTLINE_ACTIONS = frozenset(
    {"edit_plot", "review_outline", "write_outline", "rewrite_outline"}
)
_BODY_ACTIONS = frozenset(
    {"append_body", "write_body", "reset_body", "append_chapter"}
)
_CREATE_ACTIONS = frozenset({"write_outline", "write_body", "append_body"})


class ArtifactRole(str, Enum):
    OUTLINE = "outline"
    BODY = "body"
    OTHER = "other"


@dataclass(frozen=True)
class ArtifactEntry:
    filename: str
    bytes: int
    role: ArtifactRole
    exists: bool
    planned_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "bytes": self.bytes,
            "role": self.role.value,
            "exists": self.exists,
            "planned_only": self.planned_only,
        }


@dataclass(frozen=True)
class ResolvedTarget:
    filename: str
    role: ArtifactRole
    source: str
    reconciled: bool = False


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


def _state_payload(state: dict[str, Any]) -> dict[str, Any]:
    return state.get("input_payload") or state if isinstance(state.get("input_payload"), dict) else {}


def _task_id(state: dict[str, Any]) -> str:
    return str(state.get("task_id") or _state_payload(state).get("task_id") or "")


def _infer_role(filename: str, *, ms: Manuscript) -> ArtifactRole:
    name = str(filename or "")
    if ms.outline_path and name == ms.outline_path:
        return ArtifactRole.OUTLINE
    if ms.body_path and name == ms.body_path:
        return ArtifactRole.BODY
    if _is_outline_name(name):
        return ArtifactRole.OUTLINE
    return ArtifactRole.OTHER


def _file_exists(task_id: str, filename: str) -> bool:
    if not filename:
        return False
    return artifact_bytes_on_disk(task_id, filename) > 0


def _payload_mission_dict(payload: dict[str, Any]) -> dict[str, Any]:
    """Mission block from payload — ignore legacy string scalars (e.g. ``\"writing\"``)."""
    raw = payload.get("mission")
    return dict(raw) if isinstance(raw, dict) else {}


def _planned_artifacts(payload: dict[str, Any], ms: Manuscript) -> tuple[Optional[str], Optional[str]]:
    mission = _payload_mission_dict(payload)
    policy = mission.get("step_policy") if isinstance(mission.get("step_policy"), dict) else {}
    outline = None
    body = None
    if not ms.outline_path and policy.get("outline_artifact"):
        outline = str(policy["outline_artifact"])
    if not ms.body_path and policy.get("body_artifact"):
        body = str(policy["body_artifact"])
    return outline, body


def reconcile_manuscript_pointers(task_id: str, manuscript: Manuscript) -> Manuscript:
    """Refresh pointers from disk; repair stale outline/body paths."""
    files = list_task_artifacts(task_id)
    ms = Manuscript(
        task_id=task_id,
        body_path=manuscript.body_path,
        outline_path=manuscript.outline_path,
        body_bytes=manuscript.body_bytes,
        outline_bytes=manuscript.outline_bytes,
        chapter_cursor=manuscript.chapter_cursor,
        last_chapter_index=manuscript.last_chapter_index,
        revision=manuscript.revision,
        outline_revision=manuscript.outline_revision,
        body_revision=manuscript.body_revision,
        body_outline_revision_seen=manuscript.body_outline_revision_seen,
        files=files,
    )
    names = {str(f.get("filename") or "") for f in files}
    if ms.outline_path and ms.outline_path not in names:
        ms.outline_path = None
        ms.outline_bytes = 0
    if ms.body_path and ms.body_path not in names:
        ms.body_path = None
        ms.body_bytes = 0

    outline_candidates: list[dict[str, Any]] = []
    body_candidates: list[dict[str, Any]] = []
    for item in files:
        name = str(item.get("filename") or "")
        if not name or not is_manuscript_pointer_filename(name):
            continue
        if _is_outline_name(name):
            outline_candidates.append(item)
        else:
            body_candidates.append(item)

    if not ms.outline_path and outline_candidates:
        outline_candidates.sort(key=lambda x: int(x.get("bytes") or 0), reverse=True)
        pick = outline_candidates[0]
        ms.outline_path = str(pick["filename"])
        ms.outline_bytes = int(pick.get("bytes") or 0)

    if not ms.body_path and body_candidates:
        default_body = _default_body()
        by_name = {str(f["filename"]): f for f in body_candidates}
        if default_body in by_name and int(by_name[default_body].get("bytes") or 0) >= 512:
            pick = by_name[default_body]
        else:
            body_candidates.sort(key=lambda x: int(x.get("bytes") or 0), reverse=True)
            pick = body_candidates[0]
        ms.body_path = str(pick["filename"])
        ms.body_bytes = int(pick.get("bytes") or 0)

    if ms.body_path:
        ms.body_bytes = max(int(ms.body_bytes or 0), artifact_bytes_on_disk(task_id, ms.body_path))
    if ms.outline_path:
        ms.outline_bytes = max(
            int(ms.outline_bytes or 0), artifact_bytes_on_disk(task_id, ms.outline_path)
        )
    return ms


def build_artifact_manifest(
    task_id: str,
    *,
    manuscript: Manuscript | None = None,
    payload: dict[str, Any] | None = None,
) -> list[ArtifactEntry]:
    payload = payload or {}
    ms = manuscript or resolve_manuscript(task_id)
    ms = reconcile_manuscript_pointers(task_id, ms)
    planned_outline, planned_body = _planned_artifacts(payload, ms)
    entries: list[ArtifactEntry] = []
    seen: set[str] = set()

    for item in ms.files:
        name = str(item.get("filename") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        nbytes = int(item.get("bytes") or 0)
        entries.append(
            ArtifactEntry(
                filename=name,
                bytes=nbytes,
                role=_infer_role(name, ms=ms),
                exists=nbytes > 0,
            )
        )

    for planned, role in (
        (planned_outline, ArtifactRole.OUTLINE),
        (planned_body, ArtifactRole.BODY),
    ):
        if not planned or planned in seen:
            continue
        seen.add(planned)
        exists = _file_exists(task_id, planned)
        entries.append(
            ArtifactEntry(
                filename=planned,
                bytes=artifact_bytes_on_disk(task_id, planned),
                role=role,
                exists=exists,
                planned_only=not exists,
            )
        )

    return entries


def _role_for_action(action: str, target_hint: str = "") -> ArtifactRole:
    hint = str(target_hint or "").strip().lower()
    if hint == "outline":
        return ArtifactRole.OUTLINE
    if hint == "body":
        return ArtifactRole.BODY
    act = str(action or "").strip().lower()
    if act in _OUTLINE_ACTIONS:
        return ArtifactRole.OUTLINE
    if act in _BODY_ACTIONS:
        return ArtifactRole.BODY
    if act == "read":
        return ArtifactRole.BODY
    return ArtifactRole.OTHER


def _pointer_for_role(ms: Manuscript, role: ArtifactRole) -> Optional[str]:
    if role == ArtifactRole.OUTLINE:
        return ms.outline_path
    if role == ArtifactRole.BODY:
        return ms.body_path
    return None


def _command_filename(state: dict[str, Any]) -> str:
    payload = _state_payload(state)
    from app.services.writing.state_machine import get_current_command

    command = get_current_command(payload)
    if command and command.target_filename:
        return str(command.target_filename)
    return ""


def _resolve_from_disk(
    task_id: str,
    ms: Manuscript,
    role: ArtifactRole,
    *,
    reconciled: bool,
) -> Optional[ResolvedTarget]:
    ms = reconcile_manuscript_pointers(task_id, ms)
    path = _pointer_for_role(ms, role)
    if path and _file_exists(task_id, path):
        return ResolvedTarget(
            filename=path,
            role=role,
            source="disk_reconcile" if reconciled else "pointer",
            reconciled=reconciled,
        )
    return None


def _create_target(
    task_id: str,
    payload: dict[str, Any],
    ms: Manuscript,
    role: ArtifactRole,
    action: str,
) -> ResolvedTarget:
    mission = _payload_mission_dict(payload)
    policy = mission.get("step_policy") if isinstance(mission.get("step_policy"), dict) else {}
    if role == ArtifactRole.OUTLINE:
        name = _pick_artifact_basename(
            ms.outline_path,
            policy.get("outline_artifact"),
            default=_default_outline(),
        )
    elif role == ArtifactRole.BODY:
        name = _pick_artifact_basename(
            ms.body_path,
            policy.get("body_artifact"),
            policy.get("artifact_path"),
            default=_default_body(),
        )
    else:
        raise ArtifactResolutionError(
            code="role_unbound",
            message=f"Cannot create artifact for action={action} role={role.value}",
            manifest=build_artifact_manifest(task_id, manuscript=ms, payload=payload),
        )
    return ResolvedTarget(filename=name, role=role, source="create", reconciled=False)


def resolve_artifact_target(
    state: dict[str, Any],
    *,
    action: str,
    requested_filename: str = "",
    target_hint: str = "",
    require_exists: bool = True,
) -> ResolvedTarget:
    """Single entry: role pointers validated against disk; fail with manifest."""
    task_id = _task_id(state)
    if not task_id:
        raise ArtifactResolutionError(
            code="role_unbound",
            message="Missing task_id for artifact resolution",
            manifest=[],
        )

    payload = _state_payload(state)
    stored = state.get("manuscript") if isinstance(state.get("manuscript"), dict) else payload.get("manuscript")
    ms = resolve_manuscript(task_id, stored if isinstance(stored, dict) else None)
    role = _role_for_action(action, target_hint)
    manifest = build_artifact_manifest(task_id, manuscript=ms, payload=payload)

    requested = str(requested_filename or "").strip()
    if requested:
        try:
            requested = sanitize_artifact_basename(requested)
        except ValueError as exc:
            raise ArtifactResolutionError(
                code="not_found",
                message=str(exc),
                manifest=manifest,
            ) from exc
        if require_exists and not _file_exists(task_id, requested):
            raise ArtifactResolutionError(
                code="not_found",
                message=f"Artifact not found: {requested}",
                manifest=manifest,
            )
        return ResolvedTarget(
            filename=requested,
            role=_infer_role(requested, ms=ms),
            source="requested",
        )

    command_name = _command_filename(state)
    if command_name and action in _OUTLINE_ACTIONS | _BODY_ACTIONS | {"read", "edit"}:
        if require_exists and not _file_exists(task_id, command_name):
            reconciled = _resolve_from_disk(task_id, ms, role, reconciled=True)
            if reconciled:
                return reconciled
            raise ArtifactResolutionError(
                code="not_found",
                message=f"Artifact not found: {command_name}",
                manifest=manifest,
            )
        return ResolvedTarget(
            filename=command_name,
            role=_infer_role(command_name, ms=ms),
            source="command",
        )

    pointer = _pointer_for_role(ms, role)
    if pointer:
        if _file_exists(task_id, pointer):
            return ResolvedTarget(filename=pointer, role=role, source="pointer")
        reconciled = _resolve_from_disk(task_id, ms, role, reconciled=True)
        if reconciled:
            return reconciled

    disk_hit = _resolve_from_disk(task_id, ms, role, reconciled=True)
    if disk_hit:
        return disk_hit

    if not require_exists and (
        str(action).lower() in _CREATE_ACTIONS
        or (str(action).lower() == "read" and role in (ArtifactRole.OUTLINE, ArtifactRole.BODY))
    ):
        return _create_target(task_id, payload, ms, role, action)

    role_label = "大纲" if role == ArtifactRole.OUTLINE else "正文" if role == ArtifactRole.BODY else "产物"
    raise ArtifactResolutionError(
        code="not_found",
        message=f"未找到{role_label}文件（action={action}）",
        manifest=manifest,
    )


def action_for_tool(tool_name: str, state: dict[str, Any]) -> str:
    payload = _state_payload(state)
    contract = payload.get("turn_contract") or {}
    primary = str(contract.get("primary_op") or "") if isinstance(contract, dict) else ""
    if primary:
        return primary
    if tool_name == "read_text_artifact":
        return "read"
    if tool_name == "edit_text_artifact":
        return "edit_plot"
    if tool_name in ("write_text_artifact", "append_text_artifact"):
        intent = payload.get("writing_intent") or {}
        return str(intent.get("action") or "write_body")
    return "read"


def manifest_for_planning(state: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Planning input: artifact_manifest + derived steer hints."""
    task_id = _task_id(state) or str(payload.get("task_id") or "")
    ms = resolve_manuscript(task_id, state.get("manuscript") if isinstance(state.get("manuscript"), dict) else None)
    entries = build_artifact_manifest(task_id, manuscript=ms, payload=payload)
    min_outline = int(getattr(settings, "MANUSCRIPT_MIN_OUTLINE_CHARS", 80))
    outline_bytes = max(int(ms.outline_bytes or 0), 0)
    outline_entry = next((e for e in entries if e.role == ArtifactRole.OUTLINE and e.exists), None)
    return {
        "artifact_manifest": [e.to_dict() for e in entries],
        "steer_should_patch_not_rewrite": bool(outline_entry),
        "outline_complete": outline_bytes >= min_outline,
    }


def bind_planned_artifact_names(
    payload: dict[str, Any],
    *,
    planning_result: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """First-turn naming: lift planner choices into mission.step_policy only."""
    out = dict(payload)
    manuscript = out.get("manuscript") if isinstance(out.get("manuscript"), dict) else {}
    if manuscript.get("body_path") or manuscript.get("outline_path"):
        return out

    plan_intent: dict[str, Any] = {}
    if isinstance(planning_result, dict):
        raw = planning_result.get("writing_intent")
        if isinstance(raw, dict):
            plan_intent = raw

    intent = dict(out.get("writing_intent") if isinstance(out.get("writing_intent"), dict) else plan_intent)
    mission = out.get("mission") if isinstance(out.get("mission"), dict) else {}
    if str(mission.get("kind") or "") != "writing":
        return out

    policy = dict(mission.get("step_policy") or {})
    body = _pick_artifact_basename(
        policy.get("body_artifact"),
        policy.get("artifact_path"),
        intent.get("body_filename"),
        plan_intent.get("body_filename"),
        default=_default_body(),
    )
    outline = _pick_artifact_basename(
        policy.get("outline_artifact"),
        intent.get("outline_path_hint"),
        default=_default_outline(),
    )
    policy["body_artifact"] = body
    policy["outline_artifact"] = outline
    out["mission"] = {**mission, "step_policy": policy}
    return out


def outline_exists(state: dict[str, Any]) -> bool:
    task_id = _task_id(state)
    if not task_id:
        return False
    payload = _state_payload(state)
    stored = state.get("manuscript") if isinstance(state.get("manuscript"), dict) else None
    ms = resolve_manuscript(task_id, stored)
    min_outline = int(getattr(settings, "MANUSCRIPT_MIN_OUTLINE_CHARS", 80))
    for entry in build_artifact_manifest(task_id, manuscript=ms, payload=payload):
        if entry.role == ArtifactRole.OUTLINE and entry.exists:
            return True
    if ms.outline_path and int(ms.outline_bytes or 0) >= min_outline:
        return True
    if stored and int(stored.get("outline_bytes") or 0) >= min_outline:
        return bool(stored.get("outline_path"))
    return False
