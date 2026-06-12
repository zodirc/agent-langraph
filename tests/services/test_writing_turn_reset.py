"""Tests for writing operator carryover and debug.log regression cases."""

from app.runtime.state import create_initial_state
from app.services.session_turn import build_inbound_merged_payload
from app.services.writing_intent_classifier import classify_writing_operator
from app.services.writing_project import ensure_writing_project
from app.services.writing_turn_reset import resolve_writing_playbook_operator


def _manuscript_state(task_id: str, goal: str, **payload_extra):
    return create_initial_state(
        task_id=task_id,
        input_payload={
            "goal": goal,
            "target_mode": "manuscript_mode",
            "route_audit": {"inferred_kind": "manuscript", "kind_confidence": 0.9},
            **payload_extra,
        },
    )


def test_debug_log_turn2_goal_classifies_body_not_kickoff_novel(
    isolated_stores, test_settings, monkeypatch,
):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "debug-log-turn2"
    ensure_writing_project(task_id)
    goal = "请开始正文编写，写完1-10章，中间不做二次确认"
    state = _manuscript_state(task_id, goal)
    assert classify_writing_operator(goal, state) == "kickoff_body"


def test_stale_writing_operator_cleared_on_new_user_goal():
    existing = _manuscript_state(
        "carry-1",
        "基于现有素材，写一部小说，先写大纲",
        writing_operator="kickoff_novel",
    )
    merged = build_inbound_merged_payload(
        existing,
        {"goal": "请开始正文编写，写完1-10章，中间不做二次确认"},
    )
    assert merged.get("writing_operator") is None


def test_resolve_playbook_operator_prefers_fresh_on_new_turn():
    goal = "请开始正文编写，写完1-10章，中间不做二次确认"
    state = _manuscript_state("carry-2", goal)
    payload = {
        "writing_operator": "kickoff_novel",
        "target_mode": "manuscript_mode",
    }
    op = resolve_writing_playbook_operator(goal, state, payload)
    assert op == "kickoff_body"


def test_reflection_allows_kickoff_novel_outline_write():
    from app.nodes.reflection_node import reflection_node
    from app.runtime.state import merge_state

    state = merge_state(
        _manuscript_state("refl-1", "先写大纲"),
        input_payload={
            "goal": "先写大纲",
            "target_mode": "manuscript_mode",
            "writing_operator": "kickoff_novel",
            "writing_intent": {"enabled": True},
        },
        tool_results=[
            {
                "tool": "write_text_artifact",
                "status": "ok",
                "result": {"filename": "大纲.md"},
            }
        ],
    )
    result = reflection_node(state)
    issues = (result.get("reflection_result") or {}).get("issues") or []
    assert not any("大纲文件被意外修改" in str(i) for i in issues)


def test_resolve_playbook_operator_honors_pin_during_batch():
    goal = "请开始正文编写，写完1-10章，中间不做二次确认"
    state = _manuscript_state("carry-3", goal)
    payload = {
        "writing_operator": "append",
        "thin_execution_profile": "writing_batch",
        "force_write_after_reads": True,
    }
    op = resolve_writing_playbook_operator(goal, state, payload)
    assert op == "append"
