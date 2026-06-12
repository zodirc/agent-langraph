"""Tests for writing context helpers (intent + RAG excerpt injection)."""

from __future__ import annotations

from app.domain.action import Action
from app.services.writing_context import (
    applied_writing_guideline_ids,
    build_session_source_excerpt,
    build_writing_guidelines_excerpt,
    read_loop_should_force_write,
    resolve_writing_intent_for_plan,
    should_force_writing_retrieval,
    turn_has_persisted_write,
    writing_explicit_ask,
    writing_style_only_evidence,
)


def test_build_writing_guidelines_excerpt_filters_by_domain():
    state = {
        "retrieved_knowledge": [
            {
                "doc_id": "builtin-prose-voice-format",
                "content": "段落之间空一行。",
                "metadata": {"domain": "writing"},
            },
            {
                "doc_id": "code-doc",
                "content": "Use tabs.",
                "metadata": {"domain": "code"},
            },
        ]
    }
    excerpt = build_writing_guidelines_excerpt(state)
    assert "段落之间空一行" in excerpt
    assert "builtin-prose-voice-format" in excerpt
    assert "Use tabs" not in excerpt


def test_applied_writing_guideline_ids():
    state = {
        "retrieved_knowledge": [
            {"doc_id": "w1", "metadata": {"domain": "writing"}},
            {"doc_id": "c1", "metadata": {"domain": "code"}},
            {"doc_id": "w2", "metadata": {"domain": "writing"}},
        ]
    }
    assert applied_writing_guideline_ids(state) == ["w1", "w2"]


def test_resolve_writing_intent_from_write_action():
    actions = [Action(type="write_artifact", params={"filename": "novel.txt"})]
    intent = resolve_writing_intent_for_plan(
        payload={"goal": "重写小说"},
        actions=actions,
        goal="重写小说",
    )
    assert intent["enabled"] is True
    assert intent["source"] == "unified_actions"


def test_resolve_writing_intent_disabled_for_qa_style_goal():
    actions = [Action(type="answer", params={})]
    intent = resolve_writing_intent_for_plan(
        payload={"goal": "什么是大纲？"},
        actions=actions,
        goal="什么是大纲？",
    )
    assert intent["enabled"] is False


def test_should_force_writing_retrieval_when_enabled():
    assert should_force_writing_retrieval({"writing_intent": {"enabled": True}}) is True
    assert should_force_writing_retrieval({"writing_intent": {"enabled": False}}) is False


def test_read_loop_should_force_write_for_manuscript_mode():
    reads = [{"tool": "read_text_artifact", "status": "ok"} for _ in range(3)]
    state = {
        "input_payload": {
            "writing_intent": {"enabled": True},
            "target_mode": "manuscript_mode",
        }
    }
    assert read_loop_should_force_write(state, reads) is True


def test_writing_style_only_evidence():
    state = {
        "retrieved_knowledge": [
            {"doc_id": "w1", "metadata": {"domain": "writing"}},
            {"doc_id": "c1", "metadata": {"domain": "common"}},
        ]
    }
    assert writing_style_only_evidence(state) is True


def test_writing_style_only_false_with_source_domain():
    state = {
        "retrieved_knowledge": [
            {"doc_id": "w1", "metadata": {"domain": "writing"}},
            {"doc_id": "s1", "metadata": {"domain": "source"}},
        ]
    }
    assert writing_style_only_evidence(state) is False


def test_build_session_source_excerpt_filters_by_domain():
    state = {
        "retrieved_knowledge": [
            {
                "doc_id": "src-plot",
                "content": "电视剧《岁月》主线剧情。",
                "metadata": {"domain": "source"},
            },
            {
                "doc_id": "style",
                "content": "避免 AI 腔。",
                "metadata": {"domain": "writing"},
            },
        ]
    }
    excerpt = build_session_source_excerpt(state)
    assert "岁月" in excerpt
    assert "AI 腔" not in excerpt


def test_writing_explicit_ask_detected():
    assert writing_explicit_ask("需先补充剧情信息或用户提供故事大纲，方可开始撰写。") is True
    assert writing_explicit_ask("已完成第一章写作。") is False


def test_turn_has_persisted_write():
    assert turn_has_persisted_write(
        [{"tool": "write_text_artifact", "status": "ok", "result": {"bytes": 10}}]
    )
    assert not turn_has_persisted_write(
        [{"tool": "edit_text_artifact", "status": "ok", "result": {"replacements": 0}}]
    )


def test_read_loop_should_not_force_write_after_write():
    tools = [
        {"tool": "read_text_artifact", "status": "ok"},
        {"tool": "write_text_artifact", "status": "ok"},
    ]
    state = {
        "input_payload": {
            "writing_intent": {"enabled": True},
            "target_mode": "manuscript_mode",
        }
    }
    assert read_loop_should_force_write(state, tools) is False
