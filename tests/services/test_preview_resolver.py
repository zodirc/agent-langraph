"""Tests for preview resolver."""

import pytest

from app.runtime.state import merge_state
from app.services.confirmation.preview_resolver import resolve_outcome_preview


@pytest.fixture
def body_state(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.artifact_tools.task_artifact_dir",
        lambda task_id: tmp_path / task_id,
    )
    task_id = "preview-task"
    art = tmp_path / task_id
    art.mkdir(parents=True)
    body = "第一章 开头\n" + ("中间内容\n" * 50) + "第八章 结尾\n"
    (art / "novel.txt").write_text(body, encoding="utf-8")
    return merge_state(
        {
            "task_id": task_id,
            "session_id": "sess-preview",
            "mission": {"step_policy": {"body_artifact": "novel.txt"}},
            "manuscript": {"body_path": "novel.txt", "body_bytes": len(body.encode())},
            "input_payload": {},
        },
    )


def test_preview_tail_mode(body_state):
    item = {"id": "wi-1", "kind": "write_body", "params": {"preview_spec": {"mode": "tail", "max_chars": 500}}}
    preview = resolve_outcome_preview(body_state, item)
    assert preview.mode == "tail"
    assert "结尾" in preview.content


def test_preview_delta_mode(body_state):
    state = merge_state(
        body_state,
        progress={"writing_step_delta": {"excerpt": "本步新增的文字"}},
    )
    item = {"id": "wi-2", "kind": "append_chapter"}
    preview = resolve_outcome_preview(state, item)
    assert preview.mode == "delta"
    assert "本步新增" in preview.content
