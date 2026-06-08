"""Tests for StepCommitter checkpoint-first commits."""

from pathlib import Path

from app.runtime.state import create_initial_state, merge_state
from app.services.artifact_tools import task_artifact_dir
from app.services.step_committer import StepBuffer, StepCommitter


def test_step_buffer_paragraph_boundary():
    buf = StepBuffer(step_id="s1", kind="append_body", artifact_targets=["novel.txt"])
    buf.append("para one\n\npara two\n\n")
    bounds = buf.paragraph_boundaries()
    assert bounds
    flushed = buf.flush_through(bounds[0])
    assert "para one" in flushed
    assert buf.buffered_chars > 0


def test_step_committer_writes_artifact_and_state(isolated_stores, tmp_path, monkeypatch):
    state = create_initial_state(task_id="commit-task")
    isolated_stores.save(state)

    committer = StepCommitter(
        state,
        step_id="wi1_write_outline",
        kind="write_outline",
        filename="outline.txt",
        work_item_id="wi1",
        min_chars=10,
    )
    content = "# Outline\n\nChapter 1: begin the journey with enough text here."
    updated, result = committer.commit(content)
    assert result.committed_chars == len(content)
    assert updated["manuscript"]["outline_bytes"] > 0
    assert updated["interrupt_context"]["last_committed_step"]["step_id"] == "wi1_write_outline"

    art_path = task_artifact_dir("commit-task") / "outline.txt"
    assert art_path.exists()
    assert art_path.read_text(encoding="utf-8") == content


def test_partial_commit_append(isolated_stores):
    state = merge_state(
        create_initial_state(task_id="append-task"),
        manuscript={"body_path": "novel.txt", "body_bytes": 0},
    )
    isolated_stores.save(state)

    committer = StepCommitter(
        state,
        step_id="wi2_append_0",
        kind="append_body",
        filename="novel.txt",
        work_item_id="wi2",
        tool_name="append_text_artifact",
    )
    text = "First paragraph with enough content.\n\nSecond paragraph pending."
    committer.buffer.append(text)
    result_pair = committer.commit_at_paragraph_boundary()
    assert result_pair is not None
    updated, result = result_pair
    assert result.partial is True
    assert updated["manuscript"]["body_bytes"] > 0


def test_outline_block_partial_commit(isolated_stores):
    state = create_initial_state(task_id="outline-stream")
    isolated_stores.save(state)

    committer = StepCommitter(
        state,
        step_id="wi3_write_outline",
        kind="write_outline",
        filename="outline.txt",
        work_item_id="wi3",
    )
    text = "# Title\n\n## Section One\n\nContent one.\n\n## Section Two\n\nContent two."
    committer.buffer.replace_text(text)
    result_pair = committer.commit_at_outline_block_boundary()
    assert result_pair is not None
    updated, result = result_pair
    assert result.partial is True
    art_path = task_artifact_dir("outline-stream") / "outline.txt"
    assert art_path.exists()
    assert "## Section Two" not in art_path.read_text(encoding="utf-8")


def test_commit_phase_checkpoint(isolated_stores):
    from app.services.step_committer import commit_phase_checkpoint

    state = merge_state(
        create_initial_state(task_id="phase-task"),
        execution_run={"run_id": "run-phase"},
        input_payload={"current_work_item": {"id": "wi-phase"}},
        progress={
            "work_plan": {
                "items": [{"id": "wi-phase", "kind": "review_chapter", "status": "running"}],
            }
        },
    )
    isolated_stores.save(state)
    updated = commit_phase_checkpoint(
        state,
        phase="review_chapter",
        chapter=2,
        work_item_id="wi-phase",
    )
    last = updated["interrupt_context"]["last_committed_step"]
    assert last["step_id"] == "wi-phase_review_chapter_ch2"
    assert last["last_run_id"] == "run-phase"
    from app.runtime.state_field_access import progress_from_state

    row = (progress_from_state(updated) or {})["work_plan"]["items"][0]
    assert row["committed"] is True
    assert row["last_run_id"] == "run-phase"
