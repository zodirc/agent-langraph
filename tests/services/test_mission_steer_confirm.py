"""Steer intent confirmation after planning (item 1)."""

from app.runtime.state import merge_state
from app.services.mission_steer_confirm import (
    apply_steer_confirmation_pending,
    build_steer_intent_summary,
    confirm_steer_intent,
    steer_confirmation_pending,
    steer_confirmation_required,
)


def test_steer_confirmation_required_on_intervention():
    result = {"mission_intervention": {"action": "rewrite_outline", "force": True}}
    payload = {"steer_applied_at": "t", "mission": {"kind": "writing"}}
    assert steer_confirmation_required(result, payload) is True


def test_steer_confirmation_required_on_total_chars_change():
    result = {}
    payload = {
        "steer_applied_at": "t",
        "mission": {"kind": "writing", "total_target_chars": 1_200_000},
    }
    before = {"total_target_chars": 50000}
    assert steer_confirmation_required(result, payload, mission_before=before) is True


def test_build_summary_includes_intervention():
    summary = build_steer_intent_summary(
        {"mission_intervention": {"action": "rewrite_outline"}, "plan": ["outline"]},
        {"goal": "按岁月重写", "steer_applied_at": "t"},
    )
    assert "rewrite_outline" in summary["summary_text"]
    assert summary["plan"]


def test_confirm_clears_pending():
    state = merge_state(
        {"task_id": "t1", "session_id": "s1"},
        input_payload=apply_steer_confirmation_pending(
            {"steer_applied_at": "t"},
            {"summary_text": "将重写大纲"},
            task_id="t1",
        ),
    )
    assert steer_confirmation_pending(state["input_payload"])
    confirmed = confirm_steer_intent(state)
    assert confirmed["input_payload"].get("steer_intent_confirmed") is True
    assert not steer_confirmation_pending(confirmed["input_payload"])

