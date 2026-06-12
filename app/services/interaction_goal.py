"""Classify short / conversational goals vs engineering delivery requests."""

from __future__ import annotations

import re
from typing import Any, Mapping

from app.config.settings import settings
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
    r"(你正在做什么|你在做什么|你在干嘛|正在做什么|现在在做什么|"
    r"写到哪|写到哪里|当前进度|什么进度|进度如何|任务状态|现在在写什么|"
    r"what are you doing|current progress|task status)",
    re.IGNORECASE,
)

_CAPABILITY_INQUIRY_RE = re.compile(
    r"(你能做什么|你能干什么|你可以做什么|你会做什么|能做什么|有什么功能|"
    r"能帮我什么|what can you do|your capabilities)",
    re.IGNORECASE,
)

# Legacy offline fallback only — primary path is intent_observation.interaction_goal (LLM).
_SESSION_SOURCE_INQUIRY_RE = re.compile(
    r"(看过|读过|加载|导入|使用|拿到|获取|看到).{0,12}(素材|资料|材料|设定|参考|原文|剧情|人物)"
    r"|(素材|资料|材料|设定|参考).{0,12}(看过|读过|导入|加载|了么|了吗|没有|没)"
    r"|有没有.{0,8}(素材|资料|材料)"
    r"|(素材|资料).{0,6}(了么|了吗)",
    re.IGNORECASE,
)


def _interaction_goal_threshold() -> float:
    cfg = getattr(settings, "INTENT_OBSERVATION_CONFIG", None)
    if isinstance(cfg, dict) and cfg.get("interaction_goal_threshold") is not None:
        return float(cfg["interaction_goal_threshold"])
    return 0.55


def _session_source_regex_fallback_enabled() -> bool:
    return bool(getattr(settings, "SESSION_SOURCE_REGEX_FALLBACK", False))


def _goal_text(goal: str, state: Mapping[str, Any] | None) -> str:
    text = (goal or "").strip()
    if text:
        return text
    if not state:
        return ""
    payload = state.get("input_payload") or {}
    return str(payload.get("goal") or payload.get("query") or "").strip()


def _observed_interaction_goal(state: Mapping[str, Any] | None) -> tuple[str, float]:
    if not state:
        return "", 0.0
    obs = state.get("intent_observation") or {}
    if isinstance(obs, dict) and obs.get("interaction_goal"):
        return str(obs["interaction_goal"]), float(obs.get("confidence") or 0.0)
    payload = state.get("input_payload") or {}
    if isinstance(payload, dict) and payload.get("interaction_goal"):
        return (
            str(payload["interaction_goal"]),
            float(payload.get("interaction_goal_confidence") or obs.get("confidence") or 0.0),
        )
    return "", 0.0


def goal_is_capability_inquiry(goal: str) -> bool:
    """User asks what the agent can do — QA, not mission progress."""
    text = (goal or "").strip()
    return bool(text) and bool(_CAPABILITY_INQUIRY_RE.search(text))


def goal_is_session_source_inquiry(
    goal: str = "",
    state: Mapping[str, Any] | None = None,
) -> bool:
    """
    User asks whether imported session source material is available / was read.

    Primary: intent_observation.interaction_goal from the pre-planning L2 model.
    Optional legacy regex when SESSION_SOURCE_REGEX_FALLBACK is enabled.
    """
    interaction_goal, confidence = _observed_interaction_goal(state)
    threshold = _interaction_goal_threshold()
    if interaction_goal == "session_source_inquiry" and confidence >= threshold:
        return True
    if interaction_goal and interaction_goal != "session_source_inquiry" and confidence >= threshold:
        return False
    if _session_source_regex_fallback_enabled():
        text = _goal_text(goal, state)
        return bool(text) and bool(_SESSION_SOURCE_INQUIRY_RE.search(text))
    return False


def goal_is_mission_status_query(goal: str) -> bool:
    """
    Progress check while a mission is running (Cursor-style status check).

    Must not queue steer replan or mechanical append_body; answer with progress only.
    Capability questions (你能做什么) are explicitly excluded.
    """
    text = (goal or "").strip()
    if not text or goal_is_capability_inquiry(text):
        return False
    if _MISSION_STATUS_QUERY_RE.search(text):
        return True
    return False


def goal_is_pure_greeting(goal: str) -> bool:
    """True only for greeting-style openers (你好 / hi / thanks), not mixed QA tasks."""
    text = (goal or "").strip()
    return bool(text) and bool(_CONVERSATIONAL_QA_RE.match(text))


def goal_is_conversational_qa(goal: str, state: Mapping[str, Any] | None = None) -> bool:
    """
    True for greetings and other non-delivery chat in engineering/writing UI.

    Explicit interaction_mode is a preference, not a mandate to run engineering_bounded.
    Manuscript delivery/edit actions are never classified as casual QA.
    """
    text = (goal or "").strip()
    if not text:
        return True
    from app.services.writing_intent_classifier import goal_is_writing_manuscript_action
    from app.services.writing_pending import goal_looks_like_confirm_only

    if goal_is_writing_manuscript_action(text, state):
        return False
    if goal_looks_like_confirm_only(text) and state:
        from app.services.writing_pending import pending_from_payload

        payload = state.get("input_payload") or {}
        if pending_from_payload(payload):
            return False
    if _CONVERSATIONAL_QA_RE.match(text):
        return True
    if _EXPLICIT_DELIVERY_RE.search(text):
        return False
    if _ENGINEERING_SIGNAL_RE.search(text):
        return False
    from app.services.artifact_edit_intent import _EDIT_VERB_RE

    if _EDIT_VERB_RE.search(text):
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
