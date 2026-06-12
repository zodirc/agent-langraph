"""kickoff_novel operator — open-ended first-turn novel requests skip planning LLM."""

from __future__ import annotations

from unittest.mock import patch

from app.nodes.planning_node import planning_node
from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.intent_observation_policy import decide_intent_observation_policy
from app.services.writing_intent_classifier import classify_writing_operator
from app.services.writing_playbook import apply_writing_playbook
from app.services.writing_project import ensure_writing_project, outline_needs_kickoff


def _manuscript_state(task_id: str, goal: str):
    return merge_state(
        create_initial_state(
            task_id=task_id,
            input_payload={
                "goal": goal,
                "route_audit": {"inferred_kind": "manuscript", "kind_confidence": 0.9},
            },
        ),
        session_turn=1,
    )


def test_kickoff_novel_classifies_outline_first_goal(isolated_stores, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "kickoff-outline-first"
    ensure_writing_project(task_id)
    goal = (
        "基于已有素材，写一部小说，先写大纲，"
        "你要基于新的改动设定来写大纲和章节情节概览"
    )
    state = _manuscript_state(task_id, goal)
    operator = classify_writing_operator(goal, state)
    assert operator == "kickoff_novel", f"expected kickoff_novel, got {operator!r}"


def test_kickoff_novel_classifies_open_goal(isolated_stores, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "kickoff-novel-classify"
    ensure_writing_project(task_id, goal="基于现有素材写一篇小说")
    state = _manuscript_state(task_id, "基于现有素材写一篇小说")

    assert classify_writing_operator("基于现有素材写一篇小说", state) == "kickoff_novel"
    assert classify_writing_operator("写一篇小说", state) == "kickoff_novel"
    assert classify_writing_operator("开始写正文", state) == "kickoff_body"
    assert classify_writing_operator("写第一章", state) == "kickoff_body"


def test_kickoff_novel_skips_when_outline_substantial(
    isolated_stores, test_settings, monkeypatch,
):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    from app.services.artifact_tools import handle_write_text_artifact

    task_id = "kickoff-novel-has-outline"
    ensure_writing_project(task_id)
    handle_write_text_artifact(
        {
            "task_id": task_id,
            "filename": "大纲.md",
            "content": "# 大纲\n\n" + ("第一章情节展开。" * 30),
        }
    )
    assert outline_needs_kickoff(task_id) is False
    state = _manuscript_state(task_id, "写一篇小说")
    assert classify_writing_operator("写一篇小说", state) is None


def test_kickoff_novel_playbook_reads_bible_writes_outline(
    isolated_stores, test_settings, monkeypatch,
):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    from app.services.story_bible import write_story_bible

    task_id = "kickoff-novel-playbook"
    ensure_writing_project(task_id)
    write_story_bible(task_id, "# 素材卡\n\n## 主要人物\n- 张三")

    actions, plan, patched = apply_writing_playbook(
        [],
        operator="kickoff_novel",
        goal="基于现有素材写一篇小说",
        task_id=task_id,
    )
    assert patched is True
    assert [a.type for a in actions] == ["read_artifact", "write_artifact"]
    assert actions[0].params["filename"] == "素材卡.md"
    assert actions[1].params["filename"] == "大纲.md"
    assert "撰写大纲" in " ".join(plan)


def test_turn1_intent_observation_skips_llm_for_kickoff_novel(
    isolated_stores, test_settings, monkeypatch,
):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "kickoff-novel-intent-skip"
    ensure_writing_project(task_id)
    state = _manuscript_state(task_id, "基于现有素材写一篇小说")
    decision = decide_intent_observation_policy(
        state,
        explicit_mode=None,
        route_audit_seed={"inferred_kind": "manuscript", "kind_confidence": 0.9},
    )
    assert decision.invoke_model is False
    assert decision.skip_reason == "novel_kickoff_thin_path"


@patch("app.nodes.planning_node.invoke_structured")
def test_planning_thin_path_skips_llm_for_kickoff_novel(
    mock_invoke,
    isolated_stores,
    test_settings,
    monkeypatch,
):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "kickoff-novel-planning-thin"
    ensure_writing_project(task_id, goal="基于现有素材写一篇小说")
    state = merge_state(
        create_initial_state(
            task_id=task_id,
            input_payload={
                "goal": "基于现有素材写一篇小说",
                "target_mode": "manuscript_mode",
                "current_mode": "manuscript_mode",
                "route_audit": {"inferred_kind": "manuscript", "kind_confidence": 0.9},
                "pre_planning_completed": True,
                "writing_intent": {"enabled": True, "source": "manuscript_mode"},
            },
        ),
        session_turn=1,
    )
    with patch("app.nodes.planning_node.trace_enabled", return_value=False):
        out = planning_node(state)
    mock_invoke.assert_not_called()
    assert out["status"] == TaskStatus.PLANNED.value
    assert (out.get("input_payload") or {}).get("writing_operator") == "kickoff_novel"
    actions = out.get("planned_actions") or []
    types = [a.get("type") for a in actions if isinstance(a, dict)]
    assert types == ["read_artifact", "write_artifact"]
