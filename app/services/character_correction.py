"""Detect character/name correction goals and build precise multi-file edit plans."""

from __future__ import annotations

import re
from typing import Any, Sequence

from app.domain.action import Action, edit_artifact, read_artifact

_TOKEN = r"[^→\n「\"'，,、；;]{1,12}"
_ARROW_RE = re.compile(
    rf"[「\"']?({_TOKEN})[」\"']?\s*(?:→|->|—>)\s*[「\"']?({_TOKEN})[」\"']?"
)
_CHANGE_RE = re.compile(
    rf"(?:把|将)[「\"']?({_TOKEN})[」\"']?(?:改成|改为|换成)[「\"']?({_TOKEN})[」\"']?"
)
_NOT_BUT_RE = re.compile(
    rf"(?:不是|而非)[「\"']?({_TOKEN})[」\"']?(?:而是|应该是)[「\"']?({_TOKEN})[」\"']?"
)
_SHOULD_NOT_RE = re.compile(
    rf"(?:应该是|应是)[「\"']?({_TOKEN})[」\"']?(?:而不是|不是)[「\"']?({_TOKEN})[」\"']?"
)

_CHARACTER_GOAL_RE = re.compile(
    r"(?i)(角色名|人物.{0,4}改|主角|女主角|名字.{0,8}(?:不对|错误|应该|不符)|"
    r"不符合|rename\s+character|把.{1,8}(?:人物|角色).{0,4}改成)"
)


def is_character_correction_goal(goal: str) -> bool:
    text = (goal or "").strip()
    return bool(text and (_CHARACTER_GOAL_RE.search(text) or extract_name_replacements(text)))


def extract_name_replacements(goal: str) -> list[tuple[str, str]]:
    """Parse explicit old→new name pairs from the user goal."""
    text = (goal or "").strip()
    if not text:
        return []
    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str]] = []
    for pattern in (_ARROW_RE, _CHANGE_RE, _NOT_BUT_RE, _SHOULD_NOT_RE):
        for old, new in pattern.findall(text):
            old_s = old.strip()
            new_s = new.strip()
            if len(old_s) < 2 or len(new_s) < 2:
                continue
            key = (old_s, new_s)
            if key in seen:
                continue
            seen.add(key)
            pairs.append(key)
    return pairs


def _target_filenames(task_id: str, goal: str) -> list[str]:
    from app.services.artifact_resolver import build_artifact_manifest

    manifest = build_artifact_manifest(task_id)
    if not manifest:
        return []
    names = [e.filename for e in manifest]
    goal_lower = goal.lower()
    if any(k in goal_lower for k in ("story_bible", "设定", "圣经")):
        ordered = [n for n in names if "bible" in n.lower() or "设定" in n]
        ordered += [n for n in names if n not in ordered]
        return ordered
    return names


def build_character_correction_actions(
    task_id: str,
    goal: str,
    replacements: Sequence[tuple[str, str]] | None = None,
) -> list[Action]:
    """One line-numbered read per file, then batch replace_all edits."""
    pairs = list(replacements or extract_name_replacements(goal))
    if not pairs:
        return []
    edits = [{"old_text": old, "new_text": new, "replace_all": True} for old, new in pairs]
    actions: list[Action] = []
    for filename in _target_filenames(task_id, goal):
        actions.append(
            read_artifact(
                filename,
                rationale="character_correction:read",
            )
        )
        actions[-1].params["with_line_numbers"] = True
        actions.append(
            edit_artifact(
                filename,
                edits=edits,
                rationale="character_correction:edit",
            )
        )
    return actions


__all__ = [
    "build_character_correction_actions",
    "extract_name_replacements",
    "is_character_correction_goal",
]
