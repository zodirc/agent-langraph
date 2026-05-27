"""Stream manuscript/artifact text to SSE as writing_delta (separate from thinking & answer)."""

from __future__ import annotations

import time
from typing import Any, Iterator, Optional

from app.config.settings import settings
from app.services.reasoning_trace import extract_field_text, report_status_trace, trace_enabled
from app.services.stream_progress import report_writing_delta

# Throttle trace while waiting for parseable JSON "content" during artifact streaming.
_WRITING_BUFFER_TRACE_INTERVAL_SEC = 5.0
_WRITING_BUFFER_TRACE_MIN_ACCUMULATED = 80


def writing_stream_enabled() -> bool:
    return bool(getattr(settings, "WRITING_STREAM_ENABLED", True))


def writing_stream_max_chars() -> int:
    return int(getattr(settings, "WRITING_STREAM_MAX_CHARS", 200_000))


def emit_writing_content_deltas(
    accumulated: str,
    seen_len: int,
    *,
    filename: str,
    node: str = "writing",
    phase: str = "artifact",
    min_delta: int = 24,
) -> int:
    """
    Push incremental artifact ``content`` field text to writing_delta SSE.
    Returns new seen length.
    """
    if not writing_stream_enabled():
        return seen_len
    text = extract_field_text(accumulated, "content")
    if len(text) <= seen_len:
        return seen_len
    cap = writing_stream_max_chars()
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


def maybe_report_writing_buffer_trace(
    *,
    filename: str,
    accumulated_len: int,
    content_seen_len: int,
    last_report_at: float,
    interval_sec: float = _WRITING_BUFFER_TRACE_INTERVAL_SEC,
) -> float:
    """
    Emit writing/status trace while the model streams but ``content`` is not yet parseable.
    Returns updated ``last_report_at`` (monotonic clock).
    """
    if not trace_enabled() or content_seen_len > 0:
        return last_report_at
    if accumulated_len < _WRITING_BUFFER_TRACE_MIN_ACCUMULATED:
        return last_report_at
    now = time.monotonic()
    if now - last_report_at < interval_sec:
        return last_report_at
    fname = filename or "artifact"
    report_status_trace(
        "writing",
        f"模型输出中：已缓冲 {accumulated_len} 字符，{fname} 正文 content 尚未就绪"
        "（手稿区可能暂空，以此 trace 为准）",
    )
    return now


def report_writing_start(filename: str, *, target_chars: int = 0) -> None:
    if not writing_stream_enabled():
        return
    hint = f"约 {target_chars} 字" if target_chars > 0 else "生成中"
    report_writing_delta(
        node="writing",
        phase="start",
        text=f"开始生成 {filename}（{hint}）…\n",
        filename=filename,
        reset=True,
    )


def report_writing_done(filename: str, content_chars: int) -> None:
    if not writing_stream_enabled():
        return
    report_writing_delta(
        node="writing",
        phase="done",
        text=f"\n\n── 完成 {filename}（{content_chars} 字）──\n",
        filename=filename,
    )


def emit_full_content_deltas(
    filename: str,
    content: str,
    *,
    target_chars: int = 0,
    chunk_size: int = 600,
) -> None:
    """Push completed artifact text in chunks (non-streaming invoke fallback)."""
    if not writing_stream_enabled() or not content:
        return
    report_writing_start(filename, target_chars=target_chars)
    for offset in range(0, len(content), chunk_size):
        report_writing_delta(
            node="writing",
            phase="artifact",
            text=content[offset : offset + chunk_size],
            filename=filename,
        )
    report_writing_done(filename, len(content))
