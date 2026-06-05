"""Outline steer: coerce rewrite_outline → edit_plot when outline exists."""

from app.domain.writing_intent_model import WritingIntentRecord
from app.runtime.state import merge_state
from app.services.mission_intervention import coerce_steer_intervention
from app.services.outline_steer_patch import plan_edit_from_read_content


def test_coerce_write_outline_to_edit_when_outline_complete(base_state):
    state = merge_state(
        base_state,
        manuscript={"outline_path": "outline.txt", "outline_bytes": 12000, "body_bytes": 0},
        input_payload={"latest_steer_message": "使用原电影人物，不要架空人物"},
    )
    intent = WritingIntentRecord(action="write_outline", force=True, reason="planning mistake")
    out = coerce_steer_intervention(state, intent)
    assert out.action == "edit_plot"
    assert "原电影" in (out.anchor.steer_correction or "")


def test_coerce_rewrite_to_edit_when_outline_complete(base_state):
    state = merge_state(
        base_state,
        manuscript={"outline_path": "outline.txt", "outline_bytes": 12000, "body_bytes": 0},
    )
    intent = WritingIntentRecord(action="rewrite_outline", force=True, reason="user said protagonist name")
    out = coerce_steer_intervention(state, intent)
    assert out.action == "edit_plot"
    assert out.anchor.target_hint == "outline"
    assert out.anchor.steer_correction or not out.anchor.old_text


def test_coerce_keeps_rewrite_when_no_outline(base_state):
    state = merge_state(base_state, manuscript={"body_bytes": 0})
    intent = WritingIntentRecord(action="rewrite_outline", force=True)
    out = coerce_steer_intervention(state, intent)
    assert out.action == "rewrite_outline"


def test_plan_edit_from_read_content_finds_anchor(monkeypatch):
    def fake_invoke(_role, _sys, _user):
        return {
            "found": True,
            "old_text": "前联邦飞行员",
            "new_text": "月球出生",
        }

    monkeypatch.setattr("app.services.llm_client.invoke_structured", fake_invoke)
    excerpt = "林远舟：前联邦飞行员，性格冷静。\n苏棠：火星科学家。"
    plan = plan_edit_from_read_content(excerpt, "林远舟是月球出生")
    assert plan.get("found") is True
    assert plan.get("old_text") in excerpt
