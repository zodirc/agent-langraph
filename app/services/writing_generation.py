"""Writing generation lifecycle records (audit + draft meta)."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

StreamCloseStatus = Literal["ok", "partial", "aborted", "failed"]

GenerationStatus = Literal[
    "streaming",
    "partial",
    "committed",
    "failed",
    "aborted",
]


@dataclass
class WritingGenerationRecord:
    generation_id: str
    task_id: str = ""
    filename: str = "artifact.txt"
    segment_index: int = 0
    target_chars: int = 0
    status: GenerationStatus = "streaming"
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    args_bytes: int = 0
    content_bytes_parsed: int = 0
    stream_duration_ms: int = 0
    error_class: str = ""
    time_to_first_content_ms: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_meta(self) -> dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "generation_status": self.status,
            "segment_index": self.segment_index,
            "target_chars": self.target_chars,
            "args_bytes": self.args_bytes,
            "content_bytes_parsed": self.content_bytes_parsed,
            "stream_duration_ms": self.stream_duration_ms,
            "error_class": self.error_class,
            "time_to_first_content_ms": self.time_to_first_content_ms,
            **self.meta,
        }

    def finish(
        self,
        *,
        status: GenerationStatus,
        args_bytes: int = 0,
        content_bytes: int = 0,
        error_class: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.ended_at = time.time()
        self.args_bytes = args_bytes
        self.content_bytes_parsed = content_bytes
        self.stream_duration_ms = int((self.ended_at - self.started_at) * 1000)
        if error_class:
            self.error_class = error_class
        if extra:
            self.meta.update(extra)


def begin_writing_generation(
    *,
    task_id: str = "",
    filename: str = "artifact.txt",
    segment_index: int = 0,
    target_chars: int = 0,
) -> WritingGenerationRecord:
    return WritingGenerationRecord(
        generation_id=str(uuid.uuid4()),
        task_id=task_id,
        filename=filename,
        segment_index=segment_index,
        target_chars=target_chars,
    )


def map_close_status(
    *,
    has_content: bool,
    stream_interrupted: bool,
    aborted: bool = False,
) -> StreamCloseStatus:
    if aborted:
        return "aborted"
    if stream_interrupted:
        return "partial" if has_content else "failed"
    return "ok" if has_content else "failed"
