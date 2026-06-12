"""Fixed playbooks per writing operator — stabilize planner output."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.domain.action import Action
from app.services.writing_intent_classifier import WritingOperator, classify_writing_operator

_WRITE_ACTION_TYPES = frozenset({"write_artifact", "edit_artifact"})
_READ_ACTION_TYPES = frozenset({"read_artifact"})


def _resolve_body_filename(task_id: str, goal: str) -> str:
    from app.services.artifact_edit_intent import resolve_artifact_edit_filename

    resolved = resolve_artifact_edit_filename(task_id, goal)
    if resolved:
        return resolved
    from app.services.artifact_resolver import build_artifact_manifest

    manifest = build_artifact_manifest(task_id)
    for preferred in ("novel.txt", "body.txt", "正文.txt", "小说.txt"):
        for entry in manifest:
            if entry.filename.lower() == preferred:
                return entry.filename
    if len(manifest) == 1:
        return manifest[0].filename
    for entry in manifest:
        name = entry.filename.lower()
        if "outline" not in name and "大纲" not in name:
            return entry.filename
    return ""


def _resolve_outline_filename(task_id: str, goal: str) -> str:
    from app.services.artifact_resolver import build_artifact_manifest

    manifest = build_artifact_manifest(task_id)
    for entry in manifest:
        name = entry.filename.lower()
        if "outline" in name or "大纲" in name:
            return entry.filename
    if "大纲" in goal:
        return _resolve_body_filename(task_id, goal)
    return ""


def _has_side_effect_action(actions: Sequence[Action]) -> bool:
    return any(a.type in _WRITE_ACTION_TYPES for a in actions)


def _has_precise_edit(actions: Sequence[Action]) -> bool:
    for a in actions:
        if a.type != "edit_artifact":
            continue
        if str(a.params.get("old_text") or "").strip():
            return True
        edits = a.params.get("edits")
        if isinstance(edits, list) and edits:
            return True
    return False


def _only_exploratory(actions: Sequence[Action]) -> bool:
    if not actions:
        return True
    return all(a.type in _READ_ACTION_TYPES or a.type == "retrieve" for a in actions)


def _append_action(filename: str) -> Action:
    return Action(
        type="run_tool",
        params={"name": "append_text_artifact", "filename": filename},
        completes_turn=True,
        source="structural",
        rationale="playbook:append",
    )


def _write_action(filename: str) -> Action:
    return Action(
        type="write_artifact",
        params={"filename": filename},
        completes_turn=True,
        source="structural",
        rationale="playbook:rewrite",
    )


def _read_action(filename: str, *, with_line_numbers: bool = False) -> Action:
    params: dict[str, Any] = {"filename": filename}
    if with_line_numbers:
        params["with_line_numbers"] = True
    return Action(
        type="read_artifact",
        params=params,
        source="structural",
        rationale="playbook:read",
    )


def _edit_action(filename: str) -> Action:
    return Action(
        type="edit_artifact",
        params={"filename": filename},
        completes_turn=True,
        source="structural",
        rationale="playbook:edit",
    )


def playbook_plan_steps(operator: WritingOperator, filename: str) -> list[str]:
    if operator == "kickoff_body":
        return [f"读取大纲", f"撰写正文 {filename}"]
    if operator == "append":
        return [f"续写 {filename}"]
    if operator == "rewrite":
        return [f"读取 {filename}", f"全文重写 {filename}"]
    if operator == "polish":
        return [f"读取 {filename}", f"润色 {filename}"]
    if operator == "character":
        return [f"读取 {filename}", f"修改人物 {filename}"]
    if operator == "replot":
        return ["读取大纲", "修改大纲与受影响章节"]
    return []


def apply_writing_playbook(
    actions: list[Action],
    *,
    operator: WritingOperator,
    goal: str,
    task_id: str,
    state: Mapping[str, Any] | dict[str, Any] | None = None,
    force_write: bool = False,
    force_edit: bool = False,
) -> tuple[list[Action], list[str], bool]:
    """
    Normalize planner actions to operator playbook.

    Returns (actions, plan_steps, patched).
    """
    body = _resolve_body_filename(task_id, goal)
    outline = _resolve_outline_filename(task_id, goal)
    patched = False
    plan = playbook_plan_steps(operator, body or outline or "artifact")

    if _has_precise_edit(actions) and not force_write:
        return actions, plan, False

    from app.services.character_correction import (
        build_character_correction_actions,
        extract_name_replacements,
        is_character_correction_goal,
    )

    if (
        (force_write or force_edit or _only_exploratory(actions) or not _has_side_effect_action(actions))
        and (operator == "character" or is_character_correction_goal(goal))
        and extract_name_replacements(goal)
    ):
        correction = build_character_correction_actions(task_id, goal)
        if correction:
            actions = correction
            patched = True
    elif force_write or _only_exploratory(actions) or not _has_side_effect_action(actions):
        if operator == "append" and body:
            actions = [_append_action(body)]
            patched = True
        elif operator == "rewrite" and body:
            actions = [_read_action(body), _write_action(body)]
            patched = True
        elif operator in ("polish", "character", "rewrite") and body:
            # Imprecise polish/character goals need LLM-generated full text, not
            # edit_text_artifact without old_text/new_text (always 0 replacements).
            actions = [_read_action(body, with_line_numbers=True), _write_action(body)]
            patched = True
        elif operator == "kickoff_body" and outline and body:
            actions = [_read_action(outline), _write_action(body)]
            patched = True
        elif operator == "kickoff_body" and body:
            actions = [_write_action(body)]
            patched = True
        elif operator == "replot" and outline:
            actions = [_read_action(outline), _write_action(outline)]
            patched = True
        elif operator == "replot" and body:
            actions = [_read_action(body), _write_action(body)]
            patched = True
    elif operator == "append":
        # Drop redundant full-text reads; append uses tail excerpt at generation time.
        trimmed = [a for a in actions if a.type != "read_artifact"]
        if not any(
            a.type == "run_tool" and str(a.params.get("name") or "") == "append_text_artifact"
            for a in trimmed
        ):
            if body:
                trimmed.append(_append_action(body))
                patched = True
        if patched or len(trimmed) != len(actions):
            actions = trimmed
            patched = True

    return actions, plan, patched


def classify_and_apply_playbook(
    actions: list[Action],
    *,
    goal: str,
    task_id: str,
    state: Mapping[str, Any] | dict[str, Any],
    payload: dict[str, Any],
) -> tuple[list[Action], list[str], WritingOperator | None, bool]:
    """Classify operator, store on payload, and normalize actions when applicable."""
    operator = classify_writing_operator(goal, state)
    if not operator:
        existing = str(payload.get("writing_operator") or "").strip()
        if existing in ("append", "rewrite", "polish", "character", "replot", "kickoff_body"):
            operator = existing  # type: ignore[assignment]
    if not operator:
        return actions, [], None, False

    force_write = bool(payload.get("force_write_after_reads"))
    force_edit = bool(payload.get("force_edit_after_reads"))
    normalized, plan, patched = apply_writing_playbook(
        actions,
        operator=operator,
        goal=goal,
        task_id=task_id,
        state=state,
        force_write=force_write,
        force_edit=force_edit,
    )
    return normalized, plan, operator, patched
