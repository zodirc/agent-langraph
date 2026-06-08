"""Classify short / conversational goals vs engineering delivery requests."""

from __future__ import annotations

import re

from app.services.mode_router import _EXPLICIT_DELIVERY_RE, _QA_FOLLOWUP_RE

_CONVERSATIONAL_QA_RE = re.compile(
    r"^(?:你好|您好|嗨|哈喽|hi|hello|hey|在吗|谢谢|感谢|多谢|好的|没问题|嗯|哦|ok|okay)"
    r"(?:[\s!！。,，.?？~…]*)?$",
    re.IGNORECASE,
)

_ENGINEERING_SIGNAL_RE = re.compile(
    r"(?i)(落盘|生成|写出|编译|构建|项目|源码|程序|游戏|demo|makefile|"
    r"网页|html|\.cpp|\.py|c\+\+|python|工程|交付|实现一个|做一个)"
)

_MISSION_STATUS_QUERY_RE = re.compile(
    r"(你正在做什么|你在做什么|你在干嘛|在做什么|正在做什么|现在在做什么|"
    r"写到哪|写到哪里|当前进度|什么进度|进度如何|任务状态|现在在写什么|"
    r"what are you doing|current progress|task status)",
    re.IGNORECASE,
)


def goal_is_mission_status_query(goal: str) -> bool:
    """
    Meta questions during an active writing mission (Cursor-style status check).

    Must not queue steer replan or mechanical append_body; answer with progress only.
    """
    text = (goal or "").strip()
    if not text:
        return False
    if _MISSION_STATUS_QUERY_RE.search(text):
        return True
    if len(text) <= 28 and any(
        token in text for token in ("做什么", "进度", "状态", "写到哪", "在写")
    ):
        return True
    return False


def goal_is_pure_greeting(goal: str) -> bool:
    """True only for greeting-style openers (你好 / hi / thanks), not mixed QA tasks."""
    text = (goal or "").strip()
    return bool(text) and bool(_CONVERSATIONAL_QA_RE.match(text))


def goal_is_conversational_qa(goal: str) -> bool:
    """
    True for greetings and other non-delivery chat in engineering/writing UI.

    Explicit interaction_mode is a preference, not a mandate to run engineering_bounded.
    """
    text = (goal or "").strip()
    if not text:
        return True
    if _CONVERSATIONAL_QA_RE.match(text):
        return True
    if _EXPLICIT_DELIVERY_RE.search(text):
        return False
    if _ENGINEERING_SIGNAL_RE.search(text):
        return False
    if _QA_FOLLOWUP_RE.search(text):
        return True
    if len(text) <= 16 and not _ENGINEERING_SIGNAL_RE.search(text):
        return True
    return False


def explicit_mode_should_apply(mode: str, goal: str) -> bool:
    """Whether UI/API explicit interaction_mode should override inferred target_mode."""
    if mode == "engineering":
        return not goal_is_conversational_qa(goal)
    if mode in ("chat", "qa", "ask"):
        return True
    if mode == "writing":
        return not goal_is_conversational_qa(goal)
    return True
