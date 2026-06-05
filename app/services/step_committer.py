"""Step buffer and checkpoint-first artifact commit for writing tasks."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app.runtime.state import AgentState, append_audit, merge_state
from app.services.artifact_tools import handle_append_text_artifact, handle_write_text_artifact
from app.services.execution_control import (
    CONTROL_COMMITTING_STEP,
    CancelRequested,
    PauseRequested,
    allows_partial_commit,
    mark_step_boundary,
    mark_step_committed,
    raise_if_cancel_requested,
)
from app.services.manuscript_service import resolve_manuscript, sync_manuscript_snapshot_atomic, validate_manuscript_content

_PARAGRAPH_BOUNDARY = re.compile(r"\n\s*\n")
_OUTLINE_HEADING = re.compile(r"\n(?=#+\s)")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StepBuffer:
    step_id: str
    kind: str
    artifact_targets: list[str] = field(default_factory=list)
    generation_id: str = ""
    attempt: int = 1
    status: str = "running"
    buffered_chars: int = 0
    committed_chars: int = 0
    started_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    _text: str = ""

    def append(self, text: str) -> None:
        if not text:
            return
        self._text += text
        self.buffered_chars = len(self._text)
        self.updated_at = _now_iso()

    @property
    def text(self) -> str:
        return self._text

    def paragraph_boundaries(self) -> list[int]:
        """Return end offsets of complete paragraphs in buffered text."""
        ends: list[int] = []
        for match in _PARAGRAPH_BOUNDARY.finditer(self._text):
            ends.append(match.end())
        return ends

    def outline_block_boundaries(self) -> list[int]:
        """Return start offsets of markdown section headings (## …)."""
        return [match.start() for match in _OUTLINE_HEADING.finditer(self._text)]

    def replace_text(self, text: str) -> None:
        self._text = text
        self.buffered_chars = len(self._text)
        self.updated_at = _now_iso()

    def flush_through(self, end_offset: int) -> str:
        chunk = self._text[:end_offset]
        self._text = self._text[end_offset:]
        self.buffered_chars = len(self._text)
        self.updated_at = _now_iso()
        return chunk

    def to_meta(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "attempt": self.attempt,
            "status": self.status,
            "kind": self.kind,
            "artifact_targets": list(self.artifact_targets),
            "buffered_chars": self.buffered_chars,
            "committed_chars": self.committed_chars,
            "generation_id": self.generation_id,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
        }


@dataclass
class StepCommitResult:
    committed_text: str
    committed_chars: int
    artifact_outcome: dict[str, Any]
    checkpoint_ref: str
    partial: bool = False


class StepCommitter:
    """
    Checkpoint-first commit: validate → artifact → manuscript → state checkpoint.

    Order is fixed so UI/state never claim completion before disk is consistent.
    """

    def __init__(
        self,
        state: AgentState,
        *,
        step_id: str,
        kind: str,
        filename: str,
        work_item_id: str = "",
        generation_id: str = "",
        min_chars: int = 0,
        tool_name: str = "write_text_artifact",
    ) -> None:
        self.state = state
        self.task_id = str(state["task_id"])
        self.step_id = step_id
        self.kind = kind
        self.filename = filename
        self.work_item_id = work_item_id
        self.tool_name = tool_name
        self.min_chars = min_chars
        self.buffer = StepBuffer(
            step_id=step_id,
            kind=kind,
            artifact_targets=[filename],
            generation_id=generation_id or f"gen_{step_id}",
        )
        from app.services.foreground_execution import bind_step_epoch

        step_meta = bind_step_epoch(state, self.buffer.to_meta())
        self._state = mark_step_boundary(
            state,
            step_meta,
            control_state=CONTROL_COMMITTING_STEP,
        )

    def check_control(self, *, phase: str = "") -> None:
        raise_if_cancel_requested(self.task_id, phase=phase)
        from app.services.foreground_execution import assert_epoch_valid_for_commit

        step_epoch = int((self.buffer.to_meta().get("foreground_epoch") or 0))
        active = (self._state.get("interrupt_context") or {}).get("active_step") or {}
        if isinstance(active, dict) and active.get("foreground_epoch") is not None:
            step_epoch = int(active.get("foreground_epoch") or step_epoch)
        assert_epoch_valid_for_commit(self._state, step_epoch=step_epoch, phase=phase)

    def append_stream(self, text: str) -> None:
        self.check_control(phase="writing_delta")
        self.buffer.append(text)

    def commit(
        self,
        content: str | None = None,
        *,
        partial: bool = False,
        on_progress: Callable[[str], None] | None = None,
    ) -> tuple[AgentState, StepCommitResult]:
        """
        Commit buffer (or explicit content) to artifact and state checkpoint.
        """
        self.check_control(phase="pre_commit")
        text = (content if content is not None else self.buffer.text).strip()
        if not text:
            raise ValueError("StepCommitter: empty content")

        if partial:
            boundary = "outline_block" if self.kind == "write_outline" else "paragraph"
            if not allows_partial_commit(self.kind, boundary=boundary):
                raise ValueError(f"partial commit not allowed for step kind {self.kind}")

        checkpoint_ref = f"{self.step_id}:{self.buffer.generation_id}"
        from app.services.execution_control import is_step_already_committed

        if is_step_already_committed(self._state, checkpoint_ref):
            last = (self._state.get("interrupt_context") or {}).get("last_committed_step") or {}
            if int(last.get("committed_chars") or 0) >= len(text):
                outcome = {"bytes": len(text.encode("utf-8")), "path": self.filename, "idempotent": True}
                return self._state, StepCommitResult(
                    committed_text=text,
                    committed_chars=len(text),
                    artifact_outcome=outcome,
                    checkpoint_ref=checkpoint_ref,
                    partial=partial,
                )

        ok, reason = validate_manuscript_content(
            text,
            action=self.kind,
            min_chars=self.min_chars if not partial else max(1, self.min_chars // 4),
        )
        if not ok and not partial:
            raise ValueError(f"validation failed: {reason}")

        payload = self._state.get("input_payload") or {}
        constraints = list(payload.get("writing_constraints") or [])
        if constraints and not partial:
            from app.services.foreground_execution import validate_commit_guard

            guard_ok, guard_reason = validate_commit_guard(text, constraints)
            if not guard_ok:
                raise ValueError(f"commit guard failed: {guard_reason}")

        if on_progress:
            on_progress(f"提交 {self.filename}…")

        self._state = merge_state(
            self._state,
            audit_log=append_audit(
                self._state,
                "writing",
                "step_commit_started",
                {"step_id": self.step_id, "kind": self.kind, "partial": partial},
            ),
        )

        commit_started = time.monotonic()
        if self.tool_name == "append_text_artifact":
            outcome = handle_append_text_artifact(
                {"task_id": self.task_id, "filename": self.filename, "content": text}
            )
        else:
            outcome = handle_write_text_artifact(
                {"task_id": self.task_id, "filename": self.filename, "content": text}
            )

        ms = resolve_manuscript(self.task_id, self._state.get("manuscript"))
        if self.tool_name == "append_text_artifact" or self.kind == "append_body":
            ms.body_path = self.filename
            ms.body_bytes = int(outcome.get("bytes") or 0)
        elif "outline" in self.filename.lower() or self.kind == "write_outline":
            ms.outline_path = self.filename
            ms.outline_bytes = int(outcome.get("bytes") or 0)

        self.buffer.status = "committed"
        self.buffer.committed_chars = len(text)
        step_meta = self.buffer.to_meta()
        step_meta["work_item_id"] = self.work_item_id
        step_meta["partial"] = partial
        step_meta["committed_chars"] = len(text)

        updated = merge_state(
            self._state,
            manuscript=ms.to_dict(),
            audit_log=append_audit(
                self._state,
                "writing",
                "step_commit_succeeded",
                {
                    "step_id": self.step_id,
                    "kind": self.kind,
                    "filename": self.filename,
                    "committed_chars": len(text),
                    "partial": partial,
                    "checkpoint_ref": checkpoint_ref,
                },
            ),
        )
        updated = mark_step_committed(updated, step_meta, checkpoint_ref=checkpoint_ref)
        updated = sync_manuscript_snapshot_atomic(updated)
        try:
            from app.services.metrics_service import get_metrics_service

            msvc = get_metrics_service()
            msvc.observe_checkpoint_commit_ms(int((time.monotonic() - commit_started) * 1000))
            if partial:
                msvc.inc_partial_commit()
        except Exception:
            pass

        result = StepCommitResult(
            committed_text=text,
            committed_chars=len(text),
            artifact_outcome=outcome,
            checkpoint_ref=checkpoint_ref,
            partial=partial,
        )
        self._state = updated
        return updated, result

    def commit_at_paragraph_boundary(self) -> tuple[AgentState, StepCommitResult] | None:
        """Partial commit for append_body when a full paragraph is buffered."""
        if not allows_partial_commit(self.kind, boundary="paragraph"):
            return None
        bounds = self.buffer.paragraph_boundaries()
        if not bounds:
            return None
        chunk = self.buffer.flush_through(bounds[-1]).strip()
        if not chunk:
            return None
        return self.commit(chunk, partial=True)

    def commit_at_outline_block_boundary(self) -> tuple[AgentState, StepCommitResult] | None:
        """Partial commit for write_outline when a new ## section appears."""
        if not allows_partial_commit(self.kind, boundary="outline_block"):
            return None
        headings = self.buffer.outline_block_boundaries()
        if len(headings) < 2:
            return None
        end = headings[-1]
        chunk = self.buffer._text[:end].strip()
        if len(chunk) < 20:
            return None
        return self.commit(chunk, partial=True)

    def commit_from_accumulated(self, full_text: str, *, partial: bool = False) -> tuple[AgentState, StepCommitResult] | None:
        """Commit from full accumulated stream text (paragraph or outline block boundary)."""
        self.buffer.replace_text(full_text)
        if self.kind == "write_outline":
            return self.commit_at_outline_block_boundary()
        if self.kind == "append_body":
            return self.commit_at_paragraph_boundary()
        return None

    def abort_uncommitted(self) -> AgentState:
        self.buffer.status = "aborted"
        return merge_state(
            self._state,
            audit_log=append_audit(
                self._state,
                "writing",
                "step_commit_aborted",
                {"step_id": self.step_id, "buffered_chars": self.buffer.buffered_chars},
            ),
        )


def commit_generated_content(
    state: AgentState,
    *,
    step_id: str,
    kind: str,
    filename: str,
    content: str,
    work_item_id: str = "",
    tool_name: str = "write_text_artifact",
    min_chars: int = 0,
) -> AgentState:
    """Convenience wrapper for non-streaming one-shot commits."""
    committer = StepCommitter(
        state,
        step_id=step_id,
        kind=kind,
        filename=filename,
        work_item_id=work_item_id,
        tool_name=tool_name,
        min_chars=min_chars,
    )
    try:
        updated, _ = committer.commit(content)
        return updated
    except (PauseRequested, CancelRequested):
        return committer.abort_uncommitted()


def commit_phase_checkpoint(
    state: AgentState,
    *,
    phase: str,
    chapter: int,
    work_item_id: str = "",
    generation_id: str = "",
    artifact_ref: str = "",
) -> AgentState:
    """Record writing phase completion as a resumable checkpoint."""
    payload = state.get("input_payload") or {}
    item = payload.get("current_work_item") or {}
    wid = work_item_id or str(item.get("id") or "")
    step_id = f"{wid}_{phase}_ch{chapter}" if wid else f"{phase}_ch{chapter}"
    run_meta = state.get("execution_run") if isinstance(state.get("execution_run"), dict) else {}
    last = (state.get("interrupt_context") or {}).get("last_committed_step") or {}
    prev_attempt = int(last.get("attempt") or 0) if str(last.get("step_id") or "") == step_id else 0
    gen = generation_id or f"gen_{step_id}"
    step_meta = {
        "step_id": step_id,
        "kind": phase,
        "work_item_id": wid,
        "chapter_index": chapter,
        "attempt": prev_attempt + 1,
        "generation_id": gen,
        "status": "committed",
        "artifact_targets": [artifact_ref] if artifact_ref else [],
        "last_run_id": str(run_meta.get("run_id") or ""),
        "updated_at": _now_iso(),
    }
    checkpoint_ref = f"{step_id}:{gen}"
    return mark_step_committed(state, step_meta, checkpoint_ref=checkpoint_ref)


class StreamingStepSession:
    """During LLM artifact stream, buffer until finalize (staging + atomic commit)."""

    def __init__(
        self,
        state: AgentState,
        *,
        step_id: str,
        kind: str,
        filename: str,
        work_item_id: str = "",
        min_chars: int = 0,
        staging_only: bool = True,
    ) -> None:
        tool = "append_text_artifact" if kind == "append_body" else "write_text_artifact"
        self.committer = StepCommitter(
            state,
            step_id=step_id,
            kind=kind,
            filename=filename,
            work_item_id=work_item_id,
            tool_name=tool,
            min_chars=min_chars,
        )
        self._outline_blocks_committed = 0
        self._staging_only = bool(staging_only)
        self._staged_content = ""

    @classmethod
    def maybe_start(
        cls,
        trace_state: Any,
        user_payload: dict[str, Any],
        filename: str,
    ) -> Optional["StreamingStepSession"]:
        if not isinstance(trace_state, dict):
            return None
        action = str(user_payload.get("writing_action") or "").lower()
        if action not in ("write_outline", "rewrite_outline", "append_body"):
            return None
        payload = trace_state.get("input_payload") or {}
        item = payload.get("current_work_item") or {}
        work_item_id = str(item.get("id") or payload.get("work_item_id") or "")
        if action in ("write_outline", "rewrite_outline"):
            kind = "write_outline"
            step_id = f"{work_item_id}_write_outline" if work_item_id else "write_outline"
        else:
            kind = "append_body"
            step_id = f"{work_item_id}_append_body" if work_item_id else "append_body"
        from app.config.settings import settings

        min_chars = int(getattr(settings, "MANUSCRIPT_MIN_BODY_CHARS", 800))
        return cls(
            trace_state,
            step_id=step_id,
            kind=kind,
            filename=filename,
            work_item_id=work_item_id,
            min_chars=min_chars,
        )

    @property
    def state(self) -> AgentState:
        return self.committer._state

    def on_content(self, full_content: str) -> AgentState:
        self.committer.check_control(phase="stream_buffer")
        self._staged_content = full_content
        if self._staging_only:
            self.committer.buffer.replace_text(full_content)
            return self.committer._state
        if self.committer.kind == "write_outline":
            return self._commit_outline_blocks(full_content)
        result = self.committer.commit_from_accumulated(full_content)
        if result:
            self.committer._state, _ = result
        return self.committer._state

    def _commit_outline_blocks(self, content: str) -> AgentState:
        matches = list(_OUTLINE_HEADING.finditer(content))
        if len(matches) <= self._outline_blocks_committed + 1:
            return self.committer._state
        end = matches[self._outline_blocks_committed + 1].start()
        chunk = content[:end].strip()
        if len(chunk) < 20:
            return self.committer._state
        updated, _ = self.committer.commit(chunk, partial=True)
        self.committer._state = updated
        self._outline_blocks_committed += 1
        return updated

    def finalize(self, full_content: str) -> AgentState:
        text = (full_content or self._staged_content).strip()
        if not text:
            return self.committer._state
        try:
            updated, _ = self.committer.commit(text, partial=False)
            self.committer._state = updated
            return updated
        except Exception as exc:
            from app.services.foreground_execution import EpochStale

            if isinstance(exc, EpochStale):
                return self.committer.abort_uncommitted()
            raise
