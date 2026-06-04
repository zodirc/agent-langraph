"""Planning payload coercion: non-dict manuscript/mission and code basenames."""

from app.services.manuscript_service import (
    apply_planner_artifact_names,
    sync_payload_artifact_names,
)


def test_sync_payload_string_manuscript_no_get_error():
    out = sync_payload_artifact_names(
        {
            "manuscript": "novel.txt",
            "writing_intent": {"body_filename": "main.cpp"},
        }
    )
    assert "novel_filename" not in out


def test_sync_payload_code_basename_skipped_not_raised():
    out = sync_payload_artifact_names(
        {
            "writing_intent": {"body_filename": "index.html"},
        }
    )
    assert "novel_filename" not in out


def test_apply_planner_string_mission_no_get_error():
    out = apply_planner_artifact_names(
        {
            "mission": "writing",
            "goal": "demo",
            "writing_intent": {"body_filename": "game.js"},
        },
        planning_result={"writing_intent": {"body_filename": "game.js"}},
    )
    assert not isinstance(out.get("mission"), str)


def test_sync_payload_text_basename_still_promoted():
    out = sync_payload_artifact_names(
        {"writing_intent": {"body_filename": "novel.txt"}},
    )
    assert out.get("novel_filename") == "novel.txt"
