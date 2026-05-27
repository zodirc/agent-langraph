"""Optional progress hook for long-running steps (SSE uses graph_runner)."""

from __future__ import annotations

from typing import Any, Callable, Optional

_handler: Optional[Callable[[str], None]] = None
_trace_handler: Optional[Callable[[dict[str, Any]], None]] = None
_answer_handler: Optional[Callable[[dict[str, Any]], None]] = None
_thinking_handler: Optional[Callable[[dict[str, Any]], None]] = None
_writing_handler: Optional[Callable[[dict[str, Any]], None]] = None


def set_progress_handler(handler: Optional[Callable[[str], None]]) -> None:
    global _handler
    _handler = handler


def set_trace_handler(handler: Optional[Callable[[dict[str, Any]], None]]) -> None:
    global _trace_handler
    _trace_handler = handler


def set_answer_handler(handler: Optional[Callable[[dict[str, Any]], None]]) -> None:
    global _answer_handler
    _answer_handler = handler


def set_thinking_handler(handler: Optional[Callable[[dict[str, Any]], None]]) -> None:
    global _thinking_handler
    _thinking_handler = handler


def set_writing_handler(handler: Optional[Callable[[dict[str, Any]], None]]) -> None:
    global _writing_handler
    _writing_handler = handler


def get_progress_handler() -> Optional[Callable[[str], None]]:
    return _handler


def report_progress(message: str) -> None:
    if _handler:
        _handler(message)


def report_trace(
    *,
    node: str,
    phase: str,
    text: str,
    field: str = "text",
    level: str = "delta",
) -> None:
    snippet = (text or "").strip()
    if not snippet or _trace_handler is None:
        return
    _trace_handler(
        {
            "node": node,
            "phase": phase,
            "field": field,
            "text": snippet,
            "level": level,
        }
    )


def report_answer_delta(
    *,
    node: str,
    phase: str,
    text: str,
    field: str = "summary",
) -> None:
    """Push user-facing answer text (e.g. reasoning summary stream) to SSE."""
    if not text or _answer_handler is None:
        return
    _answer_handler(
        {
            "node": node,
            "phase": phase,
            "field": field,
            "text": text,
        }
    )


def report_thinking_delta(
    *,
    node: str,
    phase: str,
    text: str,
) -> None:
    """Push provider thinking-block text to SSE (separate from answer JSON)."""
    if not text or _thinking_handler is None:
        return
    _thinking_handler(
        {
            "node": node,
            "phase": phase,
            "text": text,
        }
    )


def report_writing_delta(
    *,
    node: str,
    phase: str,
    text: str,
    filename: str = "",
    reset: bool = False,
) -> None:
    """Push manuscript/artifact body text to SSE (separate from thinking & answer)."""
    if not text or _writing_handler is None:
        return
    _writing_handler(
        {
            "node": node,
            "phase": phase,
            "text": text,
            "filename": filename,
            "reset": reset,
        }
    )
