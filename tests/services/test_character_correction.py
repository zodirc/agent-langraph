"""Character/name correction planning helpers."""

from __future__ import annotations

from app.services.character_correction import (
    build_character_correction_actions,
    extract_name_replacements,
    is_character_correction_goal,
)
from app.services.writing_playbook import apply_writing_playbook


def test_extract_name_replacements_arrow_and_change():
    goal = "梁致远→梁志远，把秦池改成秦梅，主角名字不对"
    pairs = extract_name_replacements(goal)
    assert ("梁致远", "梁志远") in pairs
    assert ("秦池", "秦梅") in pairs


def test_is_character_correction_goal():
    assert is_character_correction_goal("主角名字应该是梁志远而不是秦晓")
    assert not is_character_correction_goal("续写下一章")


def test_build_character_correction_actions(isolated_stores, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    from app.services.artifact_tools import handle_write_text_artifact

    task_id = "char-fix"
    for name in ("story_bible.md", "outline.md", "chapter_01.md"):
        handle_write_text_artifact(
            {"task_id": task_id, "filename": name, "content": f"# {name}\n梁致远与秦池"}
        )
    goal = "梁致远→梁志远，秦池→秦梅"
    actions = build_character_correction_actions(task_id, goal)
    assert len(actions) == 6
    assert actions[0].type == "read_artifact"
    assert actions[0].params.get("with_line_numbers") is True
    assert actions[1].type == "edit_artifact"
    edits = actions[1].params.get("edits")
    assert len(edits) == 2
    assert edits[0]["replace_all"] is True


def test_playbook_character_correction_on_force_edit(isolated_stores, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    from app.domain.action import Action
    from app.services.artifact_tools import handle_write_text_artifact

    task_id = "char-playbook"
    handle_write_text_artifact(
        {"task_id": task_id, "filename": "story_bible.md", "content": "梁致远"}
    )
    goal = "主角名字：梁致远→梁志远"
    actions, _, patched = apply_writing_playbook(
        [Action(type="read_artifact", params={"filename": "story_bible.md"})],
        operator="character",
        goal=goal,
        task_id=task_id,
        force_edit=True,
    )
    assert patched is True
    assert any(a.type == "edit_artifact" for a in actions)
    assert not any(a.type == "write_artifact" for a in actions)
