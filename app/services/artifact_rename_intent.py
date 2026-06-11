"""Detect artifact rename intent and normalize write+rm anti-patterns to move_path."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Sequence

from app.domain.action import Action, run_tool

_RENAME_GOAL_RE = re.compile(
    r"(?i)(重命名|改名|换.{0,4}文件名|文件名.{0,6}(?:不合理|不对|改|换)|"
    r"更合理.{0,6}(?:名|文件名)|rename\s+(?:the\s+)?file|rename\s+to|"
    r"file\s+name.{0,12}(?:better|reasonable))"
)


def is_artifact_rename_goal(goal: str) -> bool:
    text = (goal or "").strip()
    return bool(text and _RENAME_GOAL_RE.search(text))


def _basename(path_or_name: str) -> str:
    raw = str(path_or_name or "").strip()
    if not raw:
        return ""
    return PurePosixPath(raw.replace("\\", "/")).name


def _write_content_empty(params: dict[str, Any]) -> bool:
    content = params.get("content")
    if content is None:
        return True
    return not str(content).strip()


def collapse_write_rm_to_move(actions: Sequence[Action]) -> list[Action]:
    """Replace write(empty)+rm_path with a single move_path when planner simulates mv."""
    items = list(actions)
    write_idx = next(
        (i for i, a in enumerate(items) if a.type == "write_artifact"),
        None,
    )
    rm_idx = next(
        (
            i
            for i, a in enumerate(items)
            if a.type == "run_tool" and str(a.params.get("name") or "") == "rm_path"
        ),
        None,
    )
    if write_idx is None or rm_idx is None:
        return items

    write = items[write_idx]
    rm = items[rm_idx]
    if not _write_content_empty(write.params):
        return items

    dst = str(write.params.get("filename") or "").strip()
    src = _basename(str(rm.params.get("path") or ""))
    if not dst or not src or src == dst:
        return items

    move = run_tool(
        "move_path",
        {"src": src, "dst": dst, "parents": False},
        rationale="rename via move_path (collapsed write+rm)",
    )
    out: list[Action] = []
    for i, action in enumerate(items):
        if i in (write_idx, rm_idx):
            continue
        out.append(action)
    insert_at = min(write_idx, rm_idx)
    out.insert(insert_at, move)
    return out


def rename_cleanup_obviated(
    tool_results: list[dict[str, Any]],
    rm_action: dict[str, Any],
) -> bool:
    """True when a new artifact was written and rm_path only cleans up the old basename."""
    rm_path = _basename(str((rm_action.get("params") or {}).get("path") or ""))
    if not rm_path:
        return False
    from app.services.tool_result_helpers import tool_result_body

    wrote_ok = {
        str(tool_result_body(item).get("path") or "").split("/")[-1]
        for item in tool_results
        if str(item.get("tool") or "") == "write_text_artifact"
        and str(item.get("status") or "ok") in ("ok", "cached")
    }
    wrote_ok |= {
        _basename(str(tool_result_body(item).get("filename") or ""))
        for item in tool_results
        if str(item.get("tool") or "") == "write_text_artifact"
        and str(item.get("status") or "ok") in ("ok", "cached")
    }
    wrote_ok.discard("")
    return bool(wrote_ok - {rm_path})


def normalize_rename_actions(
    actions: Sequence[Action],
    *,
    goal: str = "",
) -> list[Action]:
    """Apply rename-specific action normalization after LLM planning."""
    normalized = collapse_write_rm_to_move(actions)
    if is_artifact_rename_goal(goal) and len(normalized) == len(actions):
        # Planner may still emit write+rm with generated content; leave for auto-rm.
        pass
    return normalized
