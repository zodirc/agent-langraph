"""Story bible (素材卡) distillation and readiness."""

from __future__ import annotations

import threading
import time

from app.services.story_bible import (
    bootstrap_story_bible_from_session_source,
    clear_story_bible_status_for_tests,
    distill_story_bible_async,
    ensure_story_bible_ready,
    mark_story_bible_distillation_done,
    mark_story_bible_distillation_started,
    read_story_bible,
)
from app.services.writing_project import ensure_writing_project


def test_distill_async_writes_fallback_immediately(
    isolated_stores, test_settings, monkeypatch,
):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "story-bible-sync-fallback"
    ensure_writing_project(task_id)

    started = threading.Event()

    def fake_distill(**_kwargs):
        started.wait(timeout=2)

    monkeypatch.setattr(
        "app.services.story_bible._distill_story_bible",
        fake_distill,
    )
    clear_story_bible_status_for_tests()
    distill_story_bible_async(
        task_id,
        title="测试素材",
        content="主角张三在京城任职，改编要求保留官场线。" + ("更多素材细节。" * 20),
    )
    time.sleep(0.05)
    text, chars, _ = read_story_bible(task_id)
    assert chars > 80
    assert "张三" in text or "测试素材" in text
    started.set()


def test_ensure_story_bible_waits_for_pending_distillation(
    isolated_stores, test_settings, monkeypatch,
):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "story-bible-wait"
    ensure_writing_project(task_id)
    clear_story_bible_status_for_tests()
    mark_story_bible_distillation_started(task_id)

    def finish_later():
        time.sleep(0.2)
        from app.services.story_bible import write_story_bible

        write_story_bible(task_id, "# 素材卡\n\n## 主要人物\n- 李四\n" + ("情节。" * 20))
        mark_story_bible_distillation_done(task_id, chars=500)

    threading.Thread(target=finish_later, daemon=True).start()
    text, chars, _ = ensure_story_bible_ready(task_id, timeout_sec=5.0)
    assert chars >= 80
    assert "李四" in text


def test_bootstrap_from_session_source_doc(
    isolated_stores, test_settings, monkeypatch,
):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    from app.services.knowledge_store import get_knowledge_store

    task_id = "story-bible-bootstrap"
    ensure_writing_project(task_id)
    get_knowledge_store().upsert_document(
        title="会话素材",
        content="改编要求：保留双线叙事。" + ("素材正文。" * 30),
        metadata={"domain": "source"},
        doc_id=f"session-source-{task_id}",
        session_id=task_id,
    )
    assert bootstrap_story_bible_from_session_source(task_id) is True
    text, chars, _ = read_story_bible(task_id)
    assert chars >= 80
    assert "改编要求" in text
