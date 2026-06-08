"""Tests for control payload hygiene (Phase D)."""

from __future__ import annotations

from app.services.control_payload import merge_stripped_message_payload, strip_client_routing_hints


def test_strip_client_routing_hints():
    raw = {
        "goal": "hello",
        "preempt": True,
        "replace_goal": True,
        "steer_replan_mode": "rewrite",
        "confirm": True,
    }
    out = strip_client_routing_hints(raw)
    assert out["goal"] == "hello"
    assert out["confirm"] is True
    assert "preempt" not in out
    assert "replace_goal" not in out
    assert "steer_replan_mode" not in out


def test_merge_stripped_message_payload():
    out = merge_stripped_message_payload(
        {"preempt": True, "archived_mission": {"kind": "writing"}},
        text="写剧本",
        confirm=False,
    )
    assert out["goal"] == "写剧本"
    assert out["message"] == "写剧本"
    assert "preempt" not in out
    assert out.get("archived_mission")
