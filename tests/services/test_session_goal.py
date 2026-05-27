from __future__ import annotations

from app.services.session.turn_policy import (
    TurnDecision,
    apply_qa_turn_isolation,
    classify_turn_intent,
    is_ephemeral_qa_goal,
    resolve_session_turn,
    should_enter_mission_runtime,
)


def test_ephemeral_qa_goal_uses_default_suspend():
    assert is_ephemeral_qa_goal("hello")
    assert is_ephemeral_qa_goal("你好")
    assert not is_ephemeral_qa_goal("继续写下一章")


def test_classify_turn_intent_state_machine():
    assert classify_turn_intent("hello") == "isolate_qa"
    assert classify_turn_intent("继续写下一章") == "resume_mission"
    assert classify_turn_intent("写小说大纲") == "resume_mission"


def test_should_not_resume_mission_for_hello():
    state = {
        "task_id": "sess-1",
        "mission": {"kind": "writing", "objective": "写小说"},
        "execution_mode": "mission",
        "input_payload": {},
    }
    payload = {"goal": "hello", "mission_auto": True}
    assert should_enter_mission_runtime(state, payload, "hello") is False


def test_should_resume_mission_for_continue():
    state = {
        "task_id": "sess-1",
        "mission": {"kind": "writing"},
        "execution_mode": "mission",
    }
    payload = {"goal": "继续写下一章"}
    assert should_enter_mission_runtime(state, payload, "继续写下一章") is True


def test_should_resume_mission_for_substantive_steer(monkeypatch):
    state = {
        "task_id": "sess-1",
        "mission": {"kind": "writing", "objective": "写小说"},
        "execution_mode": "mission",
    }
    payload = {"goal": "把主角改成女性并调整第一章节奏"}

    def _mock_llm(goal, **kwargs):
        return {
            "turn_intent": "resume_writing",
            "confidence": 0.9,
            "reason": "writing steer",
            "source": "llm",
        }

    monkeypatch.setattr(
        "app.services.session.turn_policy.classify_turn_intent_llm",
        _mock_llm,
    )
    assert should_enter_mission_runtime(state, payload, payload["goal"]) is True


def test_resolve_session_turn_intervention():
    state = {"mission": {"kind": "writing"}}
    payload = {"goal": "调整", "mission_intervention": {"action": "edit_plot"}}
    decision = resolve_session_turn(state, payload, "调整", incoming=payload)
    assert decision.intent == "resume_mission"
    assert decision.source == "intervention"


def test_resolve_session_turn_default_suspend():
    state = {"mission": {"kind": "writing", "objective": "长篇"}}
    payload = {"goal": "你知道海贼王吗"}
    decision = resolve_session_turn(state, payload, payload["goal"], incoming=payload)
    assert decision.intent == "isolate_qa"
    assert decision.source in ("default_suspend", "pattern_fallback", "llm")


def test_qa_isolation_payload():
    existing = {"task_id": "t1", "mission": {"kind": "writing", "objective": "x"}}
    payload = apply_qa_turn_isolation({"goal": "hello"}, existing)
    assert payload["execution_mode"] == "single"
    assert payload["mission_suspended"] is True
    assert payload["archived_mission"]["kind"] == "writing"
    assert payload["writing_intent"]["enabled"] is False
