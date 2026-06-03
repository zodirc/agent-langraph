"""Preview strategy resolver — maps work_item

intervention to artifact excerpts.
Modes: full, head, tail, range, diff, delta, none (from config defaults)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.domain.mission import StepPolicy
from app.services.confirmation.config import PreviewDefault, load_confirmation_gates_config
from app.services.confirmation.snapshot import read_snapshot, unified_diff
from app.services.manuscript_service import resolve_read_paths
from app.services.mission_intervention import intervention_from_payload


@dataclass
class PreviewResult:
    filename: str
    mode: str
    content: str
    truncated: bool = False
    total_chars: int = 0
    display_label: str = "artifact_preview"

    def to_section(self) -> dict[str, Any]:
        return {
            "type": "artifact",
            "role": "preview",
            "filename": self.filename,
            "preview_mode": self.mode,
            "content": self.content,
            "truncated": self.truncated,
            "total_chars": self.total_chars,
            "display_label": self.display_label,
        }


def _read_file(task_id: str, filename: str, *, state: Optional[dict[str, Any]] = None) -> str:
    from app.services.artifact_tools import task_artifact_dir

    resolved = resolve_read_paths(state or {"task_id": task_id}, filename)
    path = task_artifact_dir(task_id) / resolved
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars] + "\n...(truncated)", True


def _resolve_filename(
    state: dict[str, Any],
    item: dict[str, Any],
    *,
    mission: dict[str, Any],
) -> str:
    kind = str(item.get("kind") or "")
    manuscript = state.get("manuscript") or {}
    policy = StepPolicy.from_dict(mission.get("step_policy") or {})
    payload = state.get("input_payload") or {}

    if kind == "write_outline":
        return str(manuscript.get("outline_path") or policy.outline_artifact or "outline.txt")
    if kind == "edit_plot":
        spec = payload.get("edit_plot_spec") or (item.get("params") or {}).get("edit_spec") or {}
        return str(spec.get("filename") or manuscript.get("body_path") or "novel.txt")
    return str(manuscript.get("body_path") or policy.body_artifact or "novel.txt")


def _default_for_kind(kind: str, intervention_action: str, cfg) -> PreviewDefault:
    preview_map = cfg.preview_defaults
    if kind in preview_map:
        return preview_map[kind]
    if intervention_action in preview_map:
        return preview_map[intervention_action]
    return PreviewDefault(mode="head", max_chars=cfg.excerpt_max_chars)


def _range_excerpt(text: str, spec: dict[str, Any], max_chars: int) -> tuple[str, bool]:
    start_line = spec.get("start_line")
    end_line = spec.get("end_line")
    old_text = str(spec.get("old_text") or "").strip()
    lines = text.splitlines(keepends=True)

    if start_line is not None and end_line is not None:
        try:
            s = max(1, int(start_line)) - 1
            e = int(end_line)
            chunk = "".join(lines[s:e])
            return _truncate(chunk.strip(), max_chars)
        except (TypeError, ValueError):
            pass

    if old_text and old_text in text:
        idx = text.index(old_text)
        pad = max(200, max_chars // 4)
        start = max(0, idx - pad)
        end = min(len(text), idx + len(old_text) + pad)
        chunk = text[start:end]
        if start > 0:
            chunk = "...(context)\n" + chunk
        if end < len(text):
            chunk = chunk + "\n...(context)"
        return _truncate(chunk, max_chars)

    return _truncate(text[:max_chars], max_chars)


def resolve_outcome_preview(
    state: dict[str, Any],
    completed_item: dict[str, Any],
) -> PreviewResult:
    cfg = load_confirmation_gates_config()
    mission = state.get("mission") or {}
    payload = state.get("input_payload") or {}
    task_id = str(state["task_id"])
    kind = str(completed_item.get("kind") or "work_item")
    intervention = intervention_from_payload(payload) or {}
    action = str(intervention.get("action") or "")

    params = dict(completed_item.get("params") or {})
    explicit = params.get("preview_spec")
    if isinstance(explicit, dict) and explicit.get("mode"):
        mode = str(explicit["mode"])
        max_chars = int(explicit.get("max_chars") or cfg.excerpt_max_chars)
        filename = str(explicit.get("filename") or _resolve_filename(state, completed_item, mission=mission))
    else:
        default = _default_for_kind(kind, action, cfg)
        mode = default.mode
        max_chars = default.max_chars
        filename = _resolve_filename(state, completed_item, mission=mission)

    progress = state.get("progress") or {}

    if mode == "delta":
        delta = progress.get("writing_step_delta") or {}
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

    if mode == "diff":
        snap_id = str(completed_item.get("id") or payload.get("last_snapshot_id") or "")
        before = read_snapshot(task_id, snap_id) if snap_id else ""
        after = _read_file(task_id, filename, state=state)
        diff_text = unified_diff(before, after, filename=filename)
        if diff_text:
            content, truncated = _truncate(diff_text, max_chars)
            return PreviewResult(
                filename=filename,
                mode="diff",
                content=content,
                truncated=truncated,
                total_chars=len(diff_text),
                display_label="artifact_diff",
            )
        mode = "head"

    text = _read_file(task_id, filename, state=state)
    total = len(text)

    if mode == "full":
        content, truncated = _truncate(text, max_chars)
    elif mode == "tail":
        from app.services.artifact_tools import read_artifact_tail

        resolved = resolve_read_paths(state, filename)
        tail = read_artifact_tail(task_id, resolved, max_chars=max_chars)
        content = tail.strip()
        truncated = total > len(content)
    elif mode == "range":
        spec = payload.get("edit_plot_spec") or params.get("edit_spec") or intervention.get("edit_spec") or {}
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
