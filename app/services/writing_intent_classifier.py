"""Classify long-form writing goals into fixed operators (playbook taxonomy)."""

from __future__ import annotations

import re
from typing import Any, Literal, Mapping

WritingOperator = Literal["append", "rewrite", "polish", "character", "replot"]

_APPEND_RE = re.compile(
    r"(?i)(续写|继续写|下一章|接着写|追加|append|continue\s+writing|next\s+chapter)"
)
_REWRITE_RE = re.compile(
    r"(?i)(全文重写|重新写|重写|重来|内容太少|太少|redo|rewrite\s+(?:the\s+)?(?:novel|story|file))"
)
_POLISH_RE = re.compile(
    r"(?i)(润色|改下措辞|措辞|polish|refine\s+(?:this\s+)?(?:paragraph|section))"
)
_CHARACTER_RE = re.compile(
    r"(?i)(角色名|人物.{0,4}改|主角|女主角|名字.{0,8}(?:不对|错误|应该|不符)|"
    r"不符合|rename\s+character|把.{1,8}(?:人物|角色).{0,4}改成)"
)
_REPLOT_RE = re.compile(
    r"(?i)(改结局|调整剧情|改剧情|改大纲|plot\s+change|change\s+(?:the\s+)?ending)"
)

_OPERATOR_ORDER: tuple[tuple[WritingOperator, re.Pattern[str]], ...] = (
    ("replot", _REPLOT_RE),
    ("character", _CHARACTER_RE),
    ("append", _APPEND_RE),
    ("rewrite", _REWRITE_RE),
    ("polish", _POLISH_RE),
)


def classify_writing_operator(
    goal: str,
    state: Mapping[str, Any] | dict[str, Any] | None = None,
) -> WritingOperator | None:
    """Return a writing operator when the goal matches manuscript edit semantics."""
    text = (goal or "").strip()
    if not text:
        return None
    payload = {}
    if state:
        payload = state.get("input_payload") or {}
        if not isinstance(payload, dict):
            payload = {}
    mode = str(payload.get("target_mode") or payload.get("current_mode") or "").lower()
    audit = payload.get("route_audit") or {}
    inferred = str(audit.get("inferred_kind") or "").lower()
    if mode != "manuscript_mode" and inferred != "manuscript":
        from app.services.artifact_edit_intent import is_artifact_edit_goal

        if not is_artifact_edit_goal(text):
            return None
    for operator, pattern in _OPERATOR_ORDER:
        if pattern.search(text):
            return operator
    from app.services.artifact_edit_intent import is_artifact_edit_goal

    if is_artifact_edit_goal(text):
        return "polish"
    return None
