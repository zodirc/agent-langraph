"""Structural revision detection — existing artifacts + edit-class verbs."""

from __future__ import annotations

import re
from typing import Any

from app.config.settings import settings

_DEFAULT_EDIT_VERBS = (
    r"润色|修改|改一下|重写|压缩|换|收|调|调整|修正|优化|"
    r"polish|revise|rewrite|retone|edit|fix|compress|refine|tweak"
)
_NO_CONTINUE_RE = re.compile(
    r"(不要继续写|只改|不要改后面|保持结构|不要扩写|别写|不要重写|"
    r"only edit|don't continue|no expand|keep structure|keep plot)",
    re.IGNORECASE,
)
_CONTINUE_RE = re.compile(
    r"(继续写|往下写|下一章|基于这一版|continue writing|write next|keep going)",
    re.IGNORECASE,
)


def revision_edit_verbs_pattern() -> re.Pattern[str]:
    cfg = getattr(settings, "REVISION_CONFIG", {}) or {}
    raw = cfg.get("edit_verbs") if isinstance(cfg, dict) else None
    if isinstance(raw, list) and raw:
        joined = "|".join(re.escape(str(v)) for v in raw)
        return re.compile(joined, re.IGNORECASE)
    return re.compile(_DEFAULT_EDIT_VERBS, re.IGNORECASE)


def has_writing_artifacts(state: dict[str, Any]) -> bool:
    task_id = str(state.get("task_id") or "")
    manuscript = state.get("manuscript") if isinstance(state.get("manuscript"), dict) else {}
    if manuscript.get("outline_path") or manuscript.get("body_path"):
        return True
    if task_id:
        from app.services.artifact_tools import list_task_artifacts

        files = list_task_artifacts(task_id)
        if files:
            return True
        from app.services.artifact_resolver import outline_exists

        if outline_exists(state):
            return True
    return False


def goal_has_edit_verb(goal: str) -> bool:
    text = (goal or "").strip()
    if not text:
        return False
    return bool(revision_edit_verbs_pattern().search(text))


def detect_structural_revision(state: dict[str, Any], goal: str) -> bool:
    """True when user likely wants local revision on existing manuscript artifacts."""
    if not goal or not has_writing_artifacts(state):
        return False
    if _CONTINUE_RE.search(goal) and not goal_has_edit_verb(goal):
        return False
    return goal_has_edit_verb(goal)


def build_revision_constraints(goal: str) -> list[str]:
    constraints: list[str] = []
    if _NO_CONTINUE_RE.search(goal or ""):
        constraints.extend(["no_continue", "no_expand"])
    if re.search(r"保持结构|keep structure", goal or "", re.IGNORECASE):
        constraints.append("keep_structure")
    if re.search(r"保持剧情|keep plot|剧情不变", goal or "", re.IGNORECASE):
        constraints.append("keep_plot")
    return constraints


def infer_revision_intent_structural(
    state: dict[str, Any],
    goal: str,
) -> dict[str, Any] | None:
    if not detect_structural_revision(state, goal):
        return None
    task_id = str(state.get("task_id") or "")
    manuscript = state.get("manuscript") if isinstance(state.get("manuscript"), dict) else {}
    from app.services.artifact_resolver import resolve_artifact_target

    target = resolve_artifact_target(state, action="edit_plot", target_hint="outline")
    filename = target.filename
    if manuscript.get("body_path") and re.search(r"章|段|对白|结尾|正文", goal):
        from app.services.manuscript_service import resolve_manuscript

        ms = resolve_manuscript(task_id, manuscript)
        if ms.body_path:
            filename = ms.body_path

    constraints = build_revision_constraints(goal)
    operation = "retone" if re.search(r"克制|tone|对白", goal, re.IGNORECASE) else "polish"
    sections: list[str] = []
    ch = re.search(r"第\s*(\d+|[一二三四五六七八九十百千]+)\s*章[^\s，,。]*", goal)
    if ch:
        sections.append(ch.group(0))
    para = re.search(r"第\s*\d+\s*段", goal)
    if para:
        sections.append(para.group(0))
    scope = "chapter" if re.search(r"章", goal) else "span"
    if re.search(r"段", goal):
        scope = "paragraph"
    if re.search(r"大纲|outline", goal, re.IGNORECASE) and not sections and scope == "span":
        scope = "full"
    elif re.search(r"全文|整篇|整个", goal):
        scope = "full"

    from app.services.intent_composer import classify_revision_override
    from app.services.revision_context import merge_continuation_revision_intent

    draft: dict[str, Any] = {
        "artifact_filename": filename,
        "artifact_role": "body" if filename == manuscript.get("body_path") else "outline",
        "revision_scope": scope,
        "target_sections": sections,
        "operation_type": operation,
        "edits": [],
        "constraints": constraints,
        "preserve_requirements": [c for c in constraints if c.startswith("keep_")],
        "output_mode": "diff",
        "replaces_active_goal": classify_revision_override(
            {**(state.get("input_payload") or {}), "goal": goal},
            state,  # type: ignore[arg-type]
        ),
        "completion_policy": "stop_after_edit",
        "source": "structural",
        "confidence": 0.82,
    }
    merged = merge_continuation_revision_intent(state, goal, draft)
    return merged
