"""Acceptance tests for writing-mode engineering contract (docs/arch.md §5.2)."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.runtime.state import TaskStatus, create_initial_state
from app.services.execution_control import OperationCancelled
from app.services.story_bible import write_story_bible
from app.services.task_control import (
    clear_all_task_control_for_tests,
    register_task_control,
    request_cancel,
)
from app.services.turn_watchdog import clear_watchdog_events_for_tests, finalize_timed_out_task


def test_source_requirements_injected_into_writing_prompt(
    isolated_stores, test_settings, monkeypatch,
):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    from app.services.writing_project import ensure_writing_project

    task_id = "accept-source-inject"
    ensure_writing_project(task_id)
    bible = (
        "# 素材卡\n\n## 改编要求（硬约束）\n"
        "- 必须保留主角姓名\n"
        "- 不得改变时代背景\n"
        "- 结局须为开放式\n"
    )
    write_story_bible(task_id, bible)

    captured: dict = {}

    def fake_draft(**kwargs):
        captured["user_payload"] = kwargs.get("user_payload") or {}
        from app.services.llm_gateway import ArtifactDraft

        return ArtifactDraft(content="第一章正文。" * 100, source="test")

    state = create_initial_state(
        task_id=task_id,
        input_payload={"goal": "开始写正文", "writing_intent": {"enabled": True}},
    )
    with patch("app.services.artifact_content.stream_artifact_draft", side_effect=fake_draft):
        from app.services.artifact_content import generate_artifact_content

        generate_artifact_content(
            state=state,
            tool_name="write_text_artifact",
            filename="正文/第001章.md",
            goal="开始写正文",
        )

    ctx = (captured.get("user_payload") or {}).get("writing_context") or {}
    excerpt = str(ctx.get("story_bible_excerpt") or "")
    for req in ("必须保留主角姓名", "不得改变时代背景", "结局须为开放式"):
        assert req in excerpt


def test_cancel_structured_invoke_within_3_seconds(isolated_stores, monkeypatch):
    clear_all_task_control_for_tests()
    task_id = "accept-cancel-structured"
    register_task_control(task_id, "run-1")

    class _Chunk:
        def __init__(self, text: str) -> None:
            self.content = text

    def slow_stream(*_args, **_kwargs):
        for i in range(80):
            time.sleep(0.04)
            yield _Chunk(f'{{"n": {i}, "plan": ["step"]}}')

    mock_llm = MagicMock()
    mock_llm.stream.side_effect = slow_stream

    def fake_get_llm(*_a, **_k):
        return mock_llm

    monkeypatch.setattr("app.services.llm_client.get_llm", fake_get_llm)
    monkeypatch.setattr("app.services.llm_client._get_cached", lambda *a, **k: None)

    from app.services.llm_client import invoke_structured

    def cancel_soon():
        time.sleep(0.2)
        request_cancel(task_id)

    threading.Thread(target=cancel_soon, daemon=True).start()
    started = time.monotonic()
    with pytest.raises((OperationCancelled, Exception)):
        invoke_structured(
            "planning",
            "system",
            '{"goal":"test"}',
            trace_state={"task_id": task_id},
        )
    elapsed = time.monotonic() - started
    assert elapsed < 3.0


def test_delete_missing_task_returns_200(isolated_stores, monkeypatch):
    from app.api import task_api

    monkeypatch.setattr(task_api, "get_state_store", lambda: isolated_stores)
    missing = "00000000-0000-0000-0000-accept-delete"
    client = TestClient(app)
    res = client.delete(f"/tasks/{missing}")
    assert res.status_code == 200
    assert res.json().get("deleted") is True


def test_watchdog_sets_timed_out_status(isolated_stores):
    clear_watchdog_events_for_tests()
    from app.runtime.state import merge_state

    task_id = "accept-watchdog"
    state = merge_state(
        create_initial_state(task_id=task_id),
        status=TaskStatus.RUNNING.value,
        current_node="writing",
    )
    isolated_stores.save(state)
    assert finalize_timed_out_task(task_id) is True
    loaded = isolated_stores.load(task_id)
    assert loaded is not None
    assert loaded.get("status") == TaskStatus.TIMED_OUT.value
    assert "超时" in str(loaded.get("final_answer") or "")
