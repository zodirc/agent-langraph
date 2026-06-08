"""Cap repeated read_text_artifact on the same task file (any extension)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from app.config.settings import settings
from app.runtime.state import AgentState


def artifact_basename(filename: str) -> str:
    return Path(str(filename or "")).name.strip()


def _filename_from_tool_result(row: dict[str, Any]) -> str:
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    for key in ("filename", "path"):
        raw = result.get(key) or row.get(key)
        if raw:
            return artifact_basename(str(raw))
    params = row.get("params") if isinstance(row.get("params"), dict) else {}
    if params.get("filename"):
        return artifact_basename(str(params["filename"]))
    return ""


def iter_tool_result_rows(state: AgentState | dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for batch in (state.get("tool_results") or [],):
        if isinstance(batch, list):
            rows.extend(r for r in batch if isinstance(r, dict))
    observations = state.get("observations")
    if isinstance(observations, list):
        for obs in observations:
            if not isinstance(obs, dict):
                continue
            nested = obs.get("tool_results")
            if isinstance(nested, list):
                rows.extend(r for r in nested if isinstance(r, dict))
    return rows


def count_artifact_reads(state: AgentState | dict[str, Any], filename: str) -> int:
    """Successful read_text_artifact invocations for this basename in session state."""
    target = artifact_basename(filename)
    if not target:
        return 0
    count = 0
    for row in iter_tool_result_rows(state):
        if str(row.get("tool") or "") != "read_text_artifact":
            continue
        status = str(row.get("status") or "ok")
        if status not in ("ok", "cached"):
            continue
        if _filename_from_tool_result(row) == target:
            count += 1
    return count


def max_reads_same_file() -> int:
    return max(1, int(getattr(settings, "ARTIFACT_MAX_READS_SAME_FILE", 2)))


def artifact_read_saturated(state: AgentState | dict[str, Any], filename: str) -> bool:
    return count_artifact_reads(state, filename) >= max_reads_same_file()


def find_last_artifact_read(
    state: AgentState | dict[str, Any],
    filename: str,
) -> Optional[dict[str, Any]]:
    target = artifact_basename(filename)
    if not target:
        return None
    for row in reversed(iter_tool_result_rows(state)):
        if str(row.get("tool") or "") != "read_text_artifact":
            continue
        if str(row.get("status") or "ok") not in ("ok", "cached"):
            continue
        if _filename_from_tool_result(row) != target:
            continue
        result = row.get("result")
        if isinstance(result, dict) and result.get("content") is not None:
            return dict(result)
    return None


def block_repeat_artifact_read(
    state: AgentState,
    *,
    filename: str,
) -> Optional[dict[str, Any]]:
    """
    Return a tool result dict when read should not hit disk again.

    None → caller may proceed with a normal read.
    """
    if not artifact_read_saturated(state, filename):
        return None
    cached = find_last_artifact_read(state, filename)
    if cached:
        return {
            **cached,
            "status": "cached",
            "read_repeat_blocked": True,
            "message": (
                f"Reused last read for {artifact_basename(filename)} "
                f"(limit {max_reads_same_file()} per file)"
            ),
        }
    return {
        "filename": artifact_basename(filename),
        "status": "blocked",
        "error": (
            f"read_text_artifact limit reached for {artifact_basename(filename)} "
            f"({max_reads_same_file()} reads)"
        ),
        "error_code": "read_repeat_limit",
        "non_retryable": True,
        "read_repeat_blocked": True,
    }


def filter_saturated_read_tools(
    state: AgentState | dict[str, Any],
    tools: list[str],
    tool_params: dict[str, Any],
) -> list[str]:
    """Drop read_text_artifact when the target file was already read enough."""
    if "read_text_artifact" not in tools:
        return tools
    cfg = tool_params.get("read_text_artifact")
    filename = ""
    if isinstance(cfg, dict):
        filename = str(cfg.get("filename") or "")
    if not filename:
        from app.services.artifact_resolver import resolve_artifact_target

        try:
            target = resolve_artifact_target(state, action="read", require_exists=False)
            filename = str(target.filename or "")
        except Exception:
            filename = ""
    if filename and artifact_read_saturated(state, filename):
        return [t for t in tools if t != "read_text_artifact"]
    return tools
