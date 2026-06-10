"""Stream artifact text to SSE as writing_delta (separate from thinking & answer)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from app.config.settings import settings
from app.services.artifact_args_parser import ArtifactArgsParser, extract_content_so_far
from app.services.reasoning_trace import extract_field_text, report_status_trace, trace_enabled
from app.services.stream_progress import report_writing_delta

if TYPE_CHECKING:
    from app.services.artifact_args_parser import ArtifactArgsParser as ArtifactArgsParserType

_BUFFER_TRACE_INTERVAL_SEC = 5.0
_BUFFER_TRACE_MIN_ACCUMULATED = 80


def artifact_stream_enabled() -> bool:
    return bool(getattr(settings, "ARTIFACT_STREAM_ENABLED", True))


def artifact_stream_max_chars() -> int:
    return int(getattr(settings, "ARTIFACT_STREAM_MAX_CHARS", 200_000))


def artifact_buffer_stall_chars() -> int:
    return int(getattr(settings, "ARTIFACT_BUFFER_STALL_CHARS", 8000))


def artifact_buffer_stall_preview_chars() -> int:
    return int(getattr(settings, "ARTIFACT_BUFFER_STALL_PREVIEW_CHARS", 200))


def extract_streaming_content(accumulated: str, parser: "ArtifactArgsParserType | None" = None) -> str:
    """Best-effort ``content`` from partial tool args (parser preferred, regex fallback)."""
    if parser is not None:
        text = parser.content_so_far()
        if text:
            return text
    text = extract_content_so_far(accumulated)
    if text:
        return text
    return extract_field_text(accumulated, "content")


def emit_artifact_content_deltas(
    accumulated: str,
    seen_len: int,
    *,
    filename: str,
    node: str = "artifact",
    phase: str = "artifact",
    min_delta: int = 24,
    parser: "ArtifactArgsParserType | None" = None,
    task_id: str | None = None,
) -> int:
    """Push incremental artifact ``content`` field text to writing_delta SSE."""
    if task_id:
        try:
            from app.services.execution_control import (
                CancelRequested,
                PauseRequested,
                check_for_control_signal,
            )

            check_for_control_signal(
                str(task_id),
                phase="writing_delta",
                raise_on_pause=True,
                raise_on_cancel=True,
            )
        except (PauseRequested, CancelRequested):
            raise
        except Exception:
            pass
    if not artifact_stream_enabled():
        return seen_len
    text = extract_streaming_content(accumulated, parser)
    if len(text) <= seen_len:
        return seen_len
    cap = artifact_stream_max_chars()
    if seen_len >= cap:
        return seen_len
    new_len = min(len(text), cap)
    allowed = new_len - seen_len
    if allowed < min_delta and new_len < len(text):
        return seen_len
    chunk = text[seen_len:new_len]
    if chunk:
        report_writing_delta(
            node=node,
            phase=phase,
            text=chunk,
            filename=filename,
        )
    return new_len


def maybe_report_artifact_buffer_trace(
    *,
    filename: str,
    accumulated_len: int,
    content_seen_len: int,
    last_report_at: float,
    interval_sec: float = _BUFFER_TRACE_INTERVAL_SEC,
    parser: "ArtifactArgsParserType | None" = None,
) -> float:
    if not trace_enabled() or content_seen_len > 0:
        return last_report_at
    if accumulated_len < _BUFFER_TRACE_MIN_ACCUMULATED:
        return last_report_at
    now = time.monotonic()
    if now - last_report_at < interval_sec:
        return last_report_at
    fname = filename or "artifact"
    msg = (
        f"模型输出中：已缓冲 {accumulated_len} 字符，{fname} 正文 content 尚未就绪"
        "（artifact 区可能暂空，以此 trace 为准）"
    )
    stall_at = artifact_buffer_stall_chars()
    if accumulated_len >= stall_at and parser is not None:
        preview = parser.stall_preview(artifact_buffer_stall_preview_chars())
        hint = "含 content 键" if parser.has_content_key() else "未见 content 键"
        if parser.discouraged_prefix_detected():
            hint += "；args 前缀含 reasoning/thinking 等字段"
        msg += f"。诊断：{hint}；前缀：{preview}"
        try:
            from app.services.metrics_service import get_metrics_service

            get_metrics_service().inc_contract_event("artifact_stream_stall")
        except Exception:
            pass
    report_status_trace("artifact", msg)
    return now


def report_artifact_stream_start(filename: str, *, target_chars: int = 0) -> None:
    if not artifact_stream_enabled():
        return
    hint = f"约 {target_chars} 字" if target_chars > 0 else "生成中"
    report_writing_delta(
        node="artifact",
        phase="start",
        text=f"开始生成 {filename}（{hint}）…\n",
        filename=filename,
        reset=True,
    )


def report_artifact_stream_done(filename: str, content_chars: int) -> None:
    if not artifact_stream_enabled():
        return
    report_writing_delta(
        node="artifact",
        phase="done",
        text=f"\n\n── 完成 {filename}（{content_chars} 字）──\n",
        filename=filename,
    )


def emit_full_artifact_content_deltas(
    filename: str,
    content: str,
    *,
    target_chars: int = 0,
    chunk_size: int = 600,
) -> None:
    """Push completed artifact text in chunks (non-streaming invoke fallback)."""
    if not artifact_stream_enabled() or not content:
        return
    report_artifact_stream_start(filename, target_chars=target_chars)
    for offset in range(0, len(content), chunk_size):
        report_writing_delta(
            node="artifact",
            phase="artifact",
            text=content[offset : offset + chunk_size],
            filename=filename,
        )
    report_artifact_stream_done(filename, len(content))
