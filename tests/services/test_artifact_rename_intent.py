"""Artifact rename intent detection and action normalization."""

from __future__ import annotations

from app.domain.action import read_artifact, run_tool, write_artifact
from app.services.artifact_rename_intent import (
    collapse_write_rm_to_move,
    is_artifact_rename_goal,
    normalize_rename_actions,
    rename_cleanup_obviated,
)


def test_is_artifact_rename_goal():
    assert is_artifact_rename_goal("你没有重命名这个文件为一个更合理的文件名")
    assert is_artifact_rename_goal("rename the file to outline.md")
    assert not is_artifact_rename_goal("润色这篇大纲")


def test_collapse_write_rm_to_move():
    actions = normalize_rename_actions(
        [
            read_artifact("深空余烬_大纲.md"),
            write_artifact("利迪策之刃_大纲.md", ""),
            run_tool("rm_path", {"path": "深空余烬_大纲.md"}),
        ],
        goal="重命名文件",
    )
    assert len(actions) == 2
    assert actions[0].type == "read_artifact"
    assert actions[1].type == "run_tool"
    assert actions[1].params["name"] == "move_path"
    assert actions[1].params["src"] == "深空余烬_大纲.md"
    assert actions[1].params["dst"] == "利迪策之刃_大纲.md"


def test_collapse_skips_when_write_has_content():
    actions = collapse_write_rm_to_move(
        [
            write_artifact("利迪策之刃_大纲.md", "正文"),
            run_tool("rm_path", {"path": "深空余烬_大纲.md"}),
        ]
    )
    assert len(actions) == 2
    assert actions[0].type == "write_artifact"


def test_rename_cleanup_obviated_string_result_safe():
    """String tool results must not crash rename_cleanup_obviated."""
    tool_results = [
        {"tool": "write_text_artifact", "status": "ok", "result": "legacy-string"},
    ]
    rm_action = {"type": "run_tool", "params": {"name": "rm_path", "path": "old.md"}}
    assert rename_cleanup_obviated(tool_results, rm_action) is False


def test_rename_cleanup_obviated():
    tool_results = [
        {
            "tool": "write_text_artifact",
            "status": "ok",
            "result": {"filename": "利迪策之刃_大纲.md", "path": "/x/利迪策之刃_大纲.md"},
        },
        {
            "tool": "rm_path",
            "status": "error",
            "error": "preview_token required",
        },
    ]
    rm_action = {"type": "run_tool", "params": {"name": "rm_path", "path": "深空余烬_大纲.md"}}
    assert rename_cleanup_obviated(tool_results, rm_action) is True
