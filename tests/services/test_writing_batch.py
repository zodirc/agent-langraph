"""Tests for multi-chapter batch continuation."""

from app.runtime.state import create_initial_state, merge_state
from app.services.artifact_tools import handle_write_text_artifact
from app.services.writing_batch import (
    apply_batch_continuation_after_tools,
    batch_chapters_written_this_turn,
    is_batch_continuation_ready,
    maybe_schedule_batch_continuation,
    prepare_auto_batch_turn,
    stamp_pending_batch_if_incomplete,
)
from app.services.writing_project import (
    CHAPTER_COMPLETION_RATIO,
    DEFAULT_BODY_FILE,
    DEFAULT_WORDS_PER_CHAPTER,
    ensure_writing_project,
    load_project,
)


def test_batch_schedules_next_chapter(isolated_stores, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "batch-write"
    ensure_writing_project(task_id, goal="写到第3章")
    min_chars = int(DEFAULT_WORDS_PER_CHAPTER * CHAPTER_COMPLETION_RATIO)
    handle_write_text_artifact(
        {
            "task_id": task_id,
            "filename": DEFAULT_BODY_FILE,
            "content": "第一章。" * (min_chars // 3 + 1) + "\n\n（第1章完）",
            "writing_operator": "kickoff_body",
        }
    )
    project = load_project(task_id)
    assert project is not None
    assert project.next_chapter == 2

    state = create_initial_state(
        task_id=task_id,
        input_payload={
            "goal": "写到第3章",
            "writing_operator": "kickoff_body",
            "writing_intent": {"enabled": True},
        },
    )
    state["tool_results"] = [
        {
            "tool": "write_text_artifact",
            "status": "ok",
            "result": {"filename": DEFAULT_BODY_FILE},
        }
    ]
    continued = maybe_schedule_batch_continuation(state)
    assert continued is not None
    assert continued.get("planned_actions")
    payload = continued.get("input_payload") or {}
    assert payload.get("writing_operator") == "append"
    assert payload.get("skip_retrieval") is True
    actions = continued.get("planned_actions") or []
    assert len(actions) == 1
    assert actions[0].get("type") == "run_tool"
    assert actions[0].get("params", {}).get("name") == "append_text_artifact"
    assert not any(a.get("type") == "read_artifact" for a in actions)


def test_batch_schedules_same_chapter_when_incomplete(isolated_stores, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "batch-incomplete"
    ensure_writing_project(task_id, goal="写到第3章")
    handle_write_text_artifact(
        {
            "task_id": task_id,
            "filename": DEFAULT_BODY_FILE,
            "content": "很短的一章。",
            "writing_operator": "kickoff_body",
        }
    )
    project = load_project(task_id)
    assert project is not None
    assert project.current_chapter_incomplete is True

    state = create_initial_state(
        task_id=task_id,
        input_payload={
            "goal": "写到第3章",
            "writing_operator": "kickoff_body",
            "writing_intent": {"enabled": True},
        },
    )
    state["tool_results"] = [
        {
            "tool": "write_text_artifact",
            "status": "ok",
            "result": {"filename": DEFAULT_BODY_FILE},
        }
    ]
    continued = maybe_schedule_batch_continuation(state)
    assert continued is not None
    assert (continued.get("input_payload") or {}).get("writing_operator") == "append"


def test_playbook_thin_path_respects_pinned_append_operator(isolated_stores, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    from app.services.writing_playbook import apply_writing_playbook

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "batch-pinned-append"
    ensure_writing_project(task_id, goal="写到第3章")
    goal = "开始写正文，写到第3章"
    actions, _plan, patched = apply_writing_playbook(
        [],
        operator="append",
        goal=goal,
        task_id=task_id,
        force_write=True,
    )
    assert patched is True
    assert len(actions) == 1
    assert actions[0].type == "run_tool"
    assert actions[0].params.get("name") == "append_text_artifact"


def test_batch_counter_increments_in_graph_checkpoint(isolated_stores, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "batch-counter"
    ensure_writing_project(task_id, goal="写到第6章")
    min_chars = int(DEFAULT_WORDS_PER_CHAPTER * CHAPTER_COMPLETION_RATIO)
    handle_write_text_artifact(
        {
            "task_id": task_id,
            "filename": DEFAULT_BODY_FILE,
            "content": "第一章。" * (min_chars // 3 + 1) + "\n\n（第1章完）",
            "writing_operator": "kickoff_body",
        }
    )

    state = create_initial_state(
        task_id=task_id,
        input_payload={
            "goal": "写到第6章",
            "writing_operator": "kickoff_body",
            "writing_intent": {"enabled": True},
        },
    )
    state["tool_results"] = [
        {
            "tool": "write_text_artifact",
            "status": "ok",
            "result": {"filename": DEFAULT_BODY_FILE},
        }
    ]
    first = apply_batch_continuation_after_tools(state)
    assert batch_chapters_written_this_turn(first) == 1
    assert is_batch_continuation_ready(first)

    first["tool_results"] = list(first.get("tool_results") or []) + [
        {
            "tool": "append_text_artifact",
            "status": "ok",
            "result": {"filename": DEFAULT_BODY_FILE},
        }
    ]
    second = apply_batch_continuation_after_tools(first)
    assert batch_chapters_written_this_turn(second) == 2


def test_batch_cap_stops_in_turn_scheduling(isolated_stores, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "batch-cap"
    ensure_writing_project(task_id, goal="写到第10章")
    state = create_initial_state(
        task_id=task_id,
        input_payload={
            "goal": "写到第10章",
            "writing_intent": {"enabled": True},
            "writing_batch_chapters_written": 4,
        },
    )
    state["tool_results"] = [
        {
            "tool": "append_text_artifact",
            "status": "ok",
            "result": {"filename": DEFAULT_BODY_FILE},
        }
    ]
    assert maybe_schedule_batch_continuation(state) is None


def test_stamp_pending_and_prepare_auto_batch_turn(isolated_stores, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "batch-auto-turn"
    ensure_writing_project(task_id, goal="写到第6章")
    project = load_project(task_id)
    assert project is not None
    project.target_chapter = 6
    project.next_chapter = 5
    from app.services.writing_project import save_project

    save_project(task_id, project)

    state = create_initial_state(
        task_id=task_id,
        session_id=task_id,
        input_payload={
            "goal": "写到第6章",
            "writing_intent": {"enabled": True},
        },
    )
    state = merge_state(state, session_turn=1, status="COMPLETED")
    state["tool_results"] = [
        {
            "tool": "append_text_artifact",
            "status": "ok",
            "result": {"filename": project.body_file},
        }
    ]
    stamped = stamp_pending_batch_if_incomplete(state)
    assert (stamped.get("input_payload") or {}).get("pending_batch_continuation")

    auto = prepare_auto_batch_turn(stamped)
    assert auto is not None
    assert auto.get("session_turn") == 2
    payload = auto.get("input_payload") or {}
    assert payload.get("writing_operator") == "append"
    assert payload.get("auto_batch_resume") is True
    assert "自动续章" in str(payload.get("goal") or "")
