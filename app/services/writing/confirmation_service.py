"""Confirmation preview from command + state snapshot (no payload guessing)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.domain.writing_command import WritingCommand
from app.services.confirmation.config import PreviewDefault, load_confirmation_gates_config
from app.services.confirmation.preview_resolver import PreviewResult, _range_excerpt, _read_file, _truncate


@dataclass
class StateSnapshot:
    task_id: str
    manuscript: dict[str, Any]
    mission: dict[str, Any]
    progress: dict[str, Any]
    payload: dict[str, Any]

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> StateSnapshot:
        return cls(
            task_id=str(state["task_id"]),
            manuscript=dict(state.get("manuscript") or {}),
            mission=dict(state.get("mission") or {}),
            progress=dict(state.get("progress") or {}),
            payload=dict(state.get("input_payload") or {}),
        )


def build_state_snapshot(state: dict[str, Any]) -> StateSnapshot:
    return StateSnapshot.from_state(state)


def _default_for_action(action: str, cfg) -> PreviewDefault:
    preview_map = cfg.preview_defaults
    if action in preview_map:
        return preview_map[action]
    return PreviewDefault(mode="head", max_chars=cfg.excerpt_max_chars)


def resolve_command_preview(
    command: WritingCommand,
    snapshot: StateSnapshot,
    *,
    completed_item: Optional[dict[str, Any]] = None,
) -> PreviewResult:
    """Preview resolver input: command + snapshot only."""
    cfg = load_confirmation_gates_config()
    filename = command.target_filename
    params = dict((completed_item or {}).get("params") or {})
    explicit = params.get("preview_spec")
    if isinstance(explicit, dict) and explicit.get("mode"):
        mode = str(explicit["mode"])
        max_chars = int(explicit.get("max_chars") or cfg.excerpt_max_chars)
        filename = str(explicit.get("filename") or filename)
    else:
        default = _default_for_action(command.action, cfg)
        mode = default.mode
        max_chars = default.max_chars

    if mode == "delta":
        delta = snapshot.progress.get("writing_step_delta") or {}
        excerpt = str(delta.get("excerpt") or "").strip()
        if excerpt:
            content, truncated = _truncate(excerpt, max_chars)
            return PreviewResult(
                filename=filename,
                mode="delta",
                content=content,
                truncated=truncated,
                total_chars=len(excerpt),
                display_label="writing_step_delta",
            )
        mode = "tail"

    state_for_read = {
        "task_id": snapshot.task_id,
        "manuscript": snapshot.manuscript,
        "input_payload": snapshot.payload,
    }
    text = _read_file(snapshot.task_id, filename, state=state_for_read)
    total = len(text)

    if mode == "full":
        content, truncated = _truncate(text, max_chars)
    elif mode == "tail":
        from app.services.artifact_tools import read_artifact_tail
        from app.services.manuscript_service import sanitize_artifact_basename

        resolved = sanitize_artifact_basename(filename)
        tail = read_artifact_tail(snapshot.task_id, resolved, max_chars=max_chars)
        content = tail.strip()
        truncated = total > len(content)
    elif mode == "range":
        spec = dict(command.edit_spec or {})
        content, truncated = _range_excerpt(text, spec, max_chars)
    elif mode == "none":
        return PreviewResult(filename=filename, mode="none", content="", total_chars=total)
    else:
        content, truncated = _truncate(text, max_chars)

    if not content.strip():
        return PreviewResult(filename=filename, mode=mode, content="", total_chars=total)

    return PreviewResult(
        filename=filename,
        mode=mode,
        content=content,
        truncated=truncated,
        total_chars=total,
    )
