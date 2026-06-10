"""Detect 'edit an existing artifact' intent (polish/revise/rewrite/continue)."""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from app.domain.action import Action

# Rewrite verbs — refine existing content, not net-new delivery or pure Q&A.
_EDIT_VERB_RE = re.compile(
    r"(?i)(润色|修改|改写|重写|改一下|改改|调整|优化|精简|扩写|续写|接着写|"
    r"重新写|再写|改成|换个|重来|重新试|再试|polish|revise|rewrite|refine|edit|improve)"
)
# Pronouns / deixis pointing at a prior artifact (optional signal).
_REFERS_PRIOR_RE = re.compile(
    r"(?i)(这个|那个|这篇|那篇|刚才|之前|上面|上一[篇个版]|它|刚写的|the file|that file|previous|above)"
)
_SAVE_HINT_RE = re.compile(
    r"(?i)(保存|写回|覆盖写|覆盖|更新文件|写入|写盘|save\s+back|overwrite)"
)
# Goal keywords → filename substrings (order: more specific first).
_FILENAME_KIND_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("故事", ("故事", "story")),
    ("散文", ("散文", "essay", "prose")),
    ("大纲", ("大纲", "outline")),
    ("正文", ("正文", "body")),
    ("章节", ("章节", "chapter")),
)


def has_existing_artifacts(state: dict[str, Any]) -> bool:
    """True when the session already has at least one artifact on disk."""
    from app.services.artifact_resolver import build_artifact_manifest

    task_id = str(state.get("task_id") or "")
    return bool(task_id) and bool(build_artifact_manifest(task_id))


def is_artifact_edit_goal(goal: str) -> bool:
    text = (goal or "").strip()
    if not text:
        return False
    return bool(_EDIT_VERB_RE.search(text))


def plan_implies_artifact_save(goal: str, plan: Sequence[str] | None) -> bool:
    """True when goal or human plan steps imply persisting an edited artifact."""
    if is_artifact_edit_goal(goal):
        return True
    blob = " ".join(str(s) for s in (plan or []))
    return bool(_SAVE_HINT_RE.search(blob))


def detect_artifact_edit_intent(state: dict[str, Any], goal: str) -> bool:
    """Edit intent is only actionable when artifacts exist to edit."""
    return is_artifact_edit_goal(goal) and has_existing_artifacts(state)


def resolve_artifact_edit_filename(task_id: str, goal: str) -> str:
    """
    Pick a single artifact basename from goal hints + on-disk manifest.

    Returns empty when ambiguous or no match (caller falls back to LLM planning).
    """
    from app.services.artifact_resolver import build_artifact_manifest

    manifest = build_artifact_manifest(task_id)
    if not manifest:
        return ""
    if len(manifest) == 1:
        return manifest[0].filename

    text = (goal or "").strip()
    if not text:
        return ""

    scored: list[tuple[int, str]] = []
    for entry in manifest:
        name = entry.filename
        name_lower = name.lower()
        stem = name.rsplit(".", 1)[0]
        score = 0
        for hint, aliases in _FILENAME_KIND_HINTS:
            if hint in text:
                if hint in name or hint in stem:
                    score += 20
            for alias in aliases:
                if alias in text.lower() and alias in name_lower:
                    score += 15
        for part in re.split(r"[_\-\s.]+", stem):
            if len(part) >= 2 and part in text:
                score += 8
        if name in text or stem in text:
            score += 25
        if score:
            scored.append((score, name))

    if not scored:
        return ""
    scored.sort(key=lambda x: (-x[0], x[1]))
    if len(scored) >= 2 and scored[0][0] == scored[1][0]:
        return ""
    return scored[0][1]


def _filename_from_read_actions(actions: Sequence[Action]) -> str:
    for action in actions:
        if action.type == "read_artifact":
            fn = str(action.params.get("filename") or "").strip()
            if fn:
                return fn
    return ""


def ensure_artifact_edit_write_action(
    actions: list[Action],
    plan: Sequence[str],
    *,
    goal: str,
    task_id: str,
) -> tuple[list[Action], bool]:
    """
    When plan/goal imply save but planner only emitted read, append write_artifact.

    Content is generated at tool execution time when params omit inline content.
    """
    if any(a.type in ("write_artifact", "edit_artifact") for a in actions):
        return actions, False
    if not plan_implies_artifact_save(goal, plan):
        return actions, False

    filename = resolve_artifact_edit_filename(task_id, goal) or _filename_from_read_actions(actions)
    if not filename:
        return actions, False

    out = list(actions)
    out.append(
        Action(
            type="write_artifact",
            params={"filename": filename},
            completes_turn=True,
            source="structural",
        )
    )
    return out, True


def artifact_edit_needs_write_after_reads(
    state: Mapping[str, Any] | dict[str, Any],
    tool_results: list[dict[str, Any]],
) -> bool:
    """True when reads happened but a save-style artifact edit is still outstanding."""
    payload = state.get("input_payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    goal = str(payload.get("goal") or payload.get("query") or "")
    plan = state.get("plan") or []
    if not plan_implies_artifact_save(goal, plan if isinstance(plan, list) else []):
        return False

    has_read = False
    has_write = False
    for item in tool_results:
        tool = str(item.get("tool") or "")
        status = str(item.get("status") or "ok")
        if status not in ("ok", "success", "cached"):
            continue
        if tool == "read_text_artifact":
            has_read = True
        if tool in ("write_text_artifact", "edit_text_artifact", "append_text_artifact"):
            has_write = True
    return has_read and not has_write
