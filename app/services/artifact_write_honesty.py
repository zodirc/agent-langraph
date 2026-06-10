"""Write-back honesty helpers — detect no-op overwrites after read→write turns."""

from __future__ import annotations

from typing import Any


def _read_sizes_from_tool_results(tool_results: list[dict[str, Any]]) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for item in tool_results or []:
        if str(item.get("tool") or "") != "read_text_artifact":
            continue
        if str(item.get("status") or "") not in ("ok", "success"):
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        filename = str(result.get("filename") or "").strip()
        if not filename:
            continue
        raw = result.get("raw_content")
        if raw is None:
            raw = result.get("content") or ""
        size = int(result.get("total_chars") or len(str(raw).encode("utf-8")))
        sizes[filename] = size
    return sizes


def write_unchanged_after_read(state: dict[str, Any]) -> bool:
    """
    True when a write_text_artifact succeeded but did not change size vs an
    earlier read of the same file in this turn (empty or identical overwrite).
    """
    tool_results = list(state.get("tool_results") or [])
    read_sizes = _read_sizes_from_tool_results(tool_results)
    if not read_sizes:
        return False

    for item in reversed(tool_results):
        if str(item.get("tool") or "") != "write_text_artifact":
            continue
        if str(item.get("status") or "") not in ("ok", "success"):
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        filename = str(result.get("filename") or "").strip()
        if not filename or filename not in read_sizes:
            continue
        written = int(result.get("bytes") or 0)
        prior = read_sizes[filename]
        if written == 0:
            return True
        if prior > 0 and written == prior:
            return True
        return False
    return False


def annotate_write_honesty(
    turn_facts: dict[str, Any],
    tool_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Set ``write_verified`` on turn_facts when a write followed a read."""
    state = {"tool_results": tool_results}
    if write_unchanged_after_read(state):
        turn_facts["write_verified"] = False
    elif any(
        str(item.get("tool") or "") == "write_text_artifact"
        and str(item.get("status") or "") in ("ok", "success")
        for item in tool_results
    ):
        turn_facts["write_verified"] = True
    return turn_facts
