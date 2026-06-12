"""Classify long-form writing goals into fixed operators (playbook taxonomy)."""

from __future__ import annotations

import re
from typing import Any, Literal, Mapping

WritingOperator = Literal["append", "rewrite", "polish", "character", "replot", "kickoff_body"]

_APPEND_RE = re.compile(
    r"(?i)(续写|继续写|下一章|接着写|追加|append|continue\s+writing|next\s+chapter)"
)
_KICKOFF_BODY_RE = re.compile(
    r"(?i)(开始写(?:正文)?|开写|写第.{0,4}章|撰写第.{0,4}章|开始撰写|"
    r"按大纲写(?:正文)?|正文写作|写正文|写小说正文|开始创作正文)"
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
    r"(?i)(改结局|调整剧情|改剧情|改.{0,8}大纲|大纲.{0,8}改|plot\s+change|change\s+(?:the\s+)?ending)"
)

_OPERATOR_ORDER: tuple[tuple[WritingOperator, re.Pattern[str]], ...] = (
    ("replot", _REPLOT_RE),
    ("character", _CHARACTER_RE),
    ("kickoff_body", _KICKOFF_BODY_RE),
    ("append", _APPEND_RE),
    ("rewrite", _REWRITE_RE),
    ("polish", _POLISH_RE),
)


def goal_is_writing_manuscript_action(
    goal: str,
    state: Mapping[str, Any] | dict[str, Any] | None = None,
) -> bool:
    """True when the goal is a concrete manuscript action (not casual chat)."""
    text = (goal or "").strip()
    if not text:
        return False
    if classify_writing_operator(text, state):
        return True
    from app.services.artifact_edit_intent import is_artifact_edit_goal

    return is_artifact_edit_goal(text)


def classify_writing_operator(
    goal: str,
    state: Mapping[str, Any] | dict[str, Any] | None = None,
) -> WritingOperator | None:
    """Return a writing operator when the goal matches manuscript edit semantics."""
    text = (goal or "").strip()
    if not text:
        return None
    for operator, pattern in _OPERATOR_ORDER:
        if pattern.search(text):
            return operator
    payload = {}
    if state:
        payload = state.get("input_payload") or {}
        if not isinstance(payload, dict):
            payload = {}
    mode = str(payload.get("target_mode") or payload.get("current_mode") or "").lower()
    audit = payload.get("route_audit") or {}
    inferred = str(audit.get("inferred_kind") or "").lower()
    from app.services.artifact_edit_intent import is_artifact_edit_goal

    if mode != "manuscript_mode" and inferred != "manuscript":
        if not is_artifact_edit_goal(text):
            return None
    if is_artifact_edit_goal(text):
        return "polish"
    return None
