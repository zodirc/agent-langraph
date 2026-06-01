"""Tests for SRDL entry gate (should_enter_react_loop)."""

from __future__ import annotations

from app.config.settings import Settings
from app.runtime.state import create_initial_state, merge_state
from app.services.react_entry import init_react_loop_state, should_enter_react_loop


def _state(**payload):
    base = create_initial_state(
        task_id="react-entry-1",
        input_payload={"goal": "分析 LangGraph 架构", "risk_level": "LOW", **payload},
    )
    return merge_state(
        base,
        plan=["retrieve facts", "call tools", "reason with evidence"],
        selected_tools=["get_runtime_info"],
    )


def test_should_not_enter_when_disabled(test_settings: Settings, monkeypatch):
    _patch_react_settings(monkeypatch, test_settings)
    test_settings.REACT_LOOP_ENABLED = False
    assert should_enter_react_loop(_state()) is False


def _patch_react_settings(monkeypatch, test_settings: Settings) -> None:
    monkeypatch.setattr("app.services.react_entry.settings", test_settings)


def test_should_enter_when_enabled_and_complex(test_settings: Settings, monkeypatch):
    _patch_react_settings(monkeypatch, test_settings)
    test_settings.REACT_LOOP_ENABLED = True
    assert should_enter_react_loop(_state()) is True


def test_force_and_disable_flags(test_settings: Settings, monkeypatch):
    _patch_react_settings(monkeypatch, test_settings)
    test_settings.REACT_LOOP_ENABLED = False
    assert should_enter_react_loop(_state(force_react_loop=True)) is True

    test_settings.REACT_LOOP_ENABLED = True
    assert should_enter_react_loop(_state(disable_react_loop=True)) is False
    assert should_enter_react_loop(_state(force_react_loop=True)) is True


def test_skips_mission_and_writing(test_settings: Settings, monkeypatch):
    test_settings.REACT_LOOP_ENABLED = True
    mission_state = merge_state(
        _state(),
        execution_mode="mission",
        input_payload={
            "goal": "long work",
            "mission": {"kind": "writing", "total_target_chars": 120000},
        },
    )
    assert should_enter_react_loop(mission_state) is False

    writing_state = merge_state(
        _state(),
        input_payload={
            "goal": "续写",
            "writing_intent": {"enabled": True, "action": "append_body"},
        },
    )
    assert should_enter_react_loop(writing_state) is False


def test_init_react_loop_state_defaults(test_settings: Settings):
    test_settings.REACT_LOOP_MAX_STEPS = 4
    test_settings.REACT_LOOP_REPLAN_ENABLED = True
    state = _state(query="test goal")
    loop = init_react_loop_state(state)
    assert loop["enabled"] is True
    assert loop["status"] == "running"
    assert loop["max_steps"] == 4
    assert "retrieve_knowledge" in loop["allowed_actions"]
    assert "finish" in loop["allowed_actions"]
