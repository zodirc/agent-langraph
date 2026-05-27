from pathlib import Path

from app.services.artifact_tools import handle_write_text_artifact
from app.services.manuscript_service import (
    build_writing_intent,
    enrich_payload,
    is_continue_writing_goal,
    resolve_manuscript,
    split_execution_tools,
    validate_manuscript_content,
)


def test_is_continue_writing_goal():
    assert is_continue_writing_goal("请继续写下一章")
    assert not is_continue_writing_goal("写一个全新故事")


def test_validate_rejects_placeholder():
    ok, reason = validate_manuscript_content("（待续写内容）", action="append_body")
    assert not ok
    assert "待续写" in reason or "placeholder" in reason


def test_resolve_manuscript_picks_largest_body(tmp_path, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", str(tmp_path / "artifacts"))
    task_id = "sess-artifact-test"
    handle_write_text_artifact(
        {"task_id": task_id, "filename": "novel.txt", "content": "x" * 2000}
    )
    handle_write_text_artifact(
        {"task_id": task_id, "filename": "恐怖小说_旧宅深渊.txt", "content": "y" * 1000}
    )
    handle_write_text_artifact(
        {"task_id": task_id, "filename": "恐怖小说大纲_旧宅深渊.txt", "content": "大纲"}
    )

    session = resolve_manuscript(task_id)
    assert session.body_path == "novel.txt"
    assert session.outline_path == "恐怖小说大纲_旧宅深渊.txt"
    assert session.body_bytes >= 2000


def test_build_writing_intent_append_on_continue():
    from app.services.manuscript_service import Manuscript

    m = Manuscript(
        task_id="t1",
        body_path="novel.txt",
        body_bytes=5000,
    )
    intent = build_writing_intent(
        goal="继续写",
        selected_tools=["append_text_artifact"],
        manuscript=m,
        session_turn=2,
    )
    assert intent["enabled"] is True
    assert intent["action"] == "append_body"


def test_split_execution_tools():
    exec_tools, writing = split_execution_tools(
        ["read_text_artifact", "append_text_artifact", "calculator"]
    )
    assert "append_text_artifact" in writing
    assert "read_text_artifact" in exec_tools
    assert "calculator" in exec_tools


def test_enrich_injects_tail(tmp_path, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", str(tmp_path / "artifacts"))
    task_id = "sess-enrich"
    body = "第一章内容。" + "续" * 500
    handle_write_text_artifact(
        {"task_id": task_id, "filename": "novel.txt", "content": body}
    )
    out = enrich_payload({"goal": "继续写"}, task_id, session_turn=2)
    assert out["novel_filename"] == "novel.txt"
    assert "previous_artifact_excerpt" in out
    assert "writing_instruction" in out
