"""Invariant tests: async close failures + mission continuation timing."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from app.runtime.state import TaskStatus, create_initial_state, merge_state
from tests.conftest import patch_task_artifact_dir
from app.services.graph_runner import GraphRunner
from app.services.mission_schema import apply_mission_step_to_payload
from app.services.session_turn import finalize_turn_history, prepare_session_turn
from app.services.state_store import get_state_store
from app.services.close_turn_async import _close_turn_sync, close_turn_async


def _enable_session(monkeypatch, test_settings):
    import app.services.session_turn as st_mod

    monkeypatch.setattr(st_mod, "settings", test_settings)
    monkeypatch.setattr("app.config.settings.settings.SESSION_ENABLED", True)


def _patch_write_turn_memories_fail(monkeypatch):
    def _boom(state):
        raise RuntimeError("simulated memory writeback failure")

    monkeypatch.setattr("app.services.conversation_context.write_turn_memories", _boom)


def _patch_reflect_and_eval_noop(monkeypatch):
    monkeypatch.setattr(
        "app.services.reflect_turn_async.reflect_turn_audit_sync",
        lambda state: state,
    )
    monkeypatch.setattr(
        "app.nodes.eval_capture_node.eval_capture_node",
        lambda state: state,
    )


def _wait_for_async_close(timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(
            t.name.startswith("close-turn-") and t.is_alive() for t in threading.enumerate()
        ):
            return
        time.sleep(0.02)
    raise AssertionError("close_turn_async thread did not finish in time")


def _completed_turn_one_state(*, session_id: str = "inv-sess-1"):
    state, _ = prepare_session_turn(
        session_id=session_id,
        user_id="u1",
        task_type="qa",
        payload={"goal": "hello"},
    )
    return finalize_turn_history(
        merge_state(
            state,
            status=TaskStatus.COMPLETED.value,
            final_answer="Hi there",
            input_payload={
                **(state.get("input_payload") or {}),
                "conversation_history": [
                    {"role": "user", "content": "hello", "at": "t1"},
                ],
            },
        )
    )


def _seed_manuscript_artifacts(
    monkeypatch,
    test_settings,
    task_id: str,
    *,
    body_bytes: int = 5000,
    outline_bytes: int = 1200,
) -> dict:
    root = Path(test_settings.ARTIFACTS_PATH)
    patch_task_artifact_dir(monkeypatch, root)
    task_dir = root / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    body_path = task_dir / "novel.txt"
    outline_path = task_dir / "outline.txt"
    body_path.write_text("x" * body_bytes, encoding="utf-8")
    outline_path.write_text("y" * outline_bytes, encoding="utf-8")
    return {
        "body_path": "novel.txt",
        "body_bytes": body_bytes,
        "outline_path": "outline.txt",
        "outline_bytes": outline_bytes,
    }


def _mission_paused_turn_one_state(*, session_id: str = "inv-mission-1", manuscript: dict):
    mission = {
        "kind": "writing",
        "objective": "写长篇",
        "step_policy": {
            "chars_per_step": 3000,
            "first_step": "outline",
            "then": "append_body",
        },
    }
    state, _ = prepare_session_turn(
        session_id=session_id,
        user_id="u1",
        task_type="qa",
        payload={
            "goal": "写长篇",
            "execution_mode": "mission",
            "mission": mission,
        },
    )
    return finalize_turn_history(
        merge_state(
            state,
            mission=mission,
            execution_mode="mission",
            mission_step=1,
            manuscript=manuscript,
            status=TaskStatus.MISSION_PAUSED.value,
            final_answer="── outline.txt ──\n\n第一章…",
            input_payload={
                **(state.get("input_payload") or {}),
                "conversation_history": [
                    {"role": "user", "content": "写长篇", "at": "t1"},
                ],
            },
        )
    )


@pytest.fixture
def sync_close_turn_async(monkeypatch):
    """Run close_turn_async inline so tests are deterministic."""

    def _inline(state):
        _close_turn_sync(state)

    monkeypatch.setattr("app.services.close_turn_async.close_turn_async", _inline)


def test_close_turn_sync_failure_records_error_without_raising(
    isolated_stores, monkeypatch, test_settings
):
    _enable_session(monkeypatch, test_settings)
    _patch_write_turn_memories_fail(monkeypatch)
    _patch_reflect_and_eval_noop(monkeypatch)

    state = _completed_turn_one_state(session_id="close-fail-1")
    _close_turn_sync(state)

    loaded = get_state_store().load("close-fail-1")
    assert loaded is not None
    bg = loaded.get("background_status") or {}
    assert bg.get("turn_closed") is False
    assert "simulated memory writeback failure" in str(bg.get("turn_close_error") or "")
    audit_nodes = [e.get("node") for e in loaded.get("audit_log") or []]
    assert "turn_closed" in audit_nodes
    assert loaded.get("final_answer") == "Hi there"


def test_close_turn_async_failure_does_not_raise_to_caller(
    isolated_stores, monkeypatch, test_settings
):
    _enable_session(monkeypatch, test_settings)
    _patch_write_turn_memories_fail(monkeypatch)
    _patch_reflect_and_eval_noop(monkeypatch)

    state = _completed_turn_one_state(session_id="close-async-fail-1")
    close_turn_async(state)
    _wait_for_async_close()

    loaded = get_state_store().load("close-async-fail-1")
    assert loaded is not None
    bg = loaded.get("background_status") or {}
    assert bg.get("turn_closed") is False


def test_next_session_turn_after_writeback_failure(
    isolated_stores,
    monkeypatch,
    test_settings,
    sync_close_turn_async,
):
    """Sync history is persisted in _finalize_turn; async memory failure must not block turn 2."""
    _enable_session(monkeypatch, test_settings)
    _patch_write_turn_memories_fail(monkeypatch)
    _patch_reflect_and_eval_noop(monkeypatch)

    store = get_state_store()
    state1 = _completed_turn_one_state(session_id="next-turn-1")
    finalized = GraphRunner()._finalize_turn(state1)
    store.save(finalized)

    state2, created2 = prepare_session_turn(
        session_id="next-turn-1",
        user_id="u1",
        task_type="qa",
        payload={"goal": "what did I say?"},
    )
    assert created2 is False
    assert state2["session_turn"] == 2
    history = state2["input_payload"]["conversation_history"]
    roles = [m["role"] for m in history]
    assert "assistant" in roles
    assert any("Hi there" in str(m.get("content") or "") for m in history if m["role"] == "assistant")


def test_next_session_turn_after_async_writeback_failure(
    isolated_stores, monkeypatch, test_settings
):
    """Fire-and-forget close failure must not prevent the following inbound message."""
    _enable_session(monkeypatch, test_settings)
    _patch_write_turn_memories_fail(monkeypatch)
    _patch_reflect_and_eval_noop(monkeypatch)

    store = get_state_store()
    state1 = _completed_turn_one_state(session_id="next-turn-async-1")
    finalized = GraphRunner()._finalize_turn(state1)
    store.save(finalized)
    _wait_for_async_close()

    state2, created2 = prepare_session_turn(
        session_id="next-turn-async-1",
        user_id="u1",
        task_type="qa",
        payload={"goal": "continue"},
    )
    assert created2 is False
    assert state2["session_turn"] == 2
    assert state2["input_payload"]["conversation_history"][-1]["role"] == "user"


def test_mission_continue_after_finalize_with_writeback_failure(
    isolated_stores,
    monkeypatch,
    test_settings,
    sync_close_turn_async,
):
    """Mission manuscript/step must survive delivered→async-close even when writeback fails."""
    _enable_session(monkeypatch, test_settings)
    _patch_write_turn_memories_fail(monkeypatch)
    _patch_reflect_and_eval_noop(monkeypatch)
    manuscript = _seed_manuscript_artifacts(monkeypatch, test_settings, "mission-cont-1")

    store = get_state_store()
    state1 = _mission_paused_turn_one_state(
        session_id="mission-cont-1", manuscript=manuscript
    )
    finalized = GraphRunner()._finalize_turn(state1)
    store.save(finalized)

    state2, created2 = prepare_session_turn(
        session_id="mission-cont-1",
        user_id="u1",
        task_type="qa",
        payload={"goal": "继续写正文"},
    )
    assert created2 is False
    assert state2["session_turn"] == 2
    assert (state2.get("input_payload") or {}).get("mission") is not None
    assert state2.get("execution_mode") == "mission"
    ms = state2.get("manuscript") or {}
    assert ms.get("body_bytes") == 5000
    assert ms.get("outline_bytes") == 1200

    payload2 = apply_mission_step_to_payload(state2)
    intent = payload2.get("writing_intent") or {}
    assert intent.get("enabled") is True
    assert payload2.get("manuscript", {}).get("body_path") == "novel.txt"


def test_mission_continue_from_output_checkpoint_before_async_close(
    isolated_stores, monkeypatch, test_settings
):
    """
    At delivered (output node save), mission/manuscript must already be readable
    for the next turn — must not depend on async memory_writeback.
    """
    _enable_session(monkeypatch, test_settings)
    manuscript = _seed_manuscript_artifacts(monkeypatch, test_settings, "mission-output-1")

    store = get_state_store()
    mission = {
        "kind": "writing",
        "objective": "写长篇",
        "step_policy": {"chars_per_step": 3000, "first_step": "outline", "then": "append_body"},
    }
    state, _ = prepare_session_turn(
        session_id="mission-output-1",
        user_id="u1",
        task_type="qa",
        payload={"goal": "写长篇", "execution_mode": "mission", "mission": mission},
    )
    output_checkpoint = merge_state(
        state,
        mission=mission,
        execution_mode="mission",
        mission_step=1,
        manuscript=manuscript,
        status=TaskStatus.MISSION_PAUSED.value,
        final_answer="── outline.txt ──\n\n第一章…",
        current_node="output",
    )
    store.save(output_checkpoint)

    state2, created2 = prepare_session_turn(
        session_id="mission-output-1",
        user_id="u1",
        task_type="qa",
        payload={"goal": "继续"},
    )
    assert created2 is False
    assert state2["session_turn"] == 2
    assert (state2.get("manuscript") or {}).get("body_bytes") == 5000
    assert (state2.get("input_payload") or {}).get("mission") is not None

    payload2 = apply_mission_step_to_payload(state2)
    assert (payload2.get("writing_intent") or {}).get("enabled") is True


def test_finalize_turn_persists_history_before_async_close(
    isolated_stores,
    monkeypatch,
    test_settings,
):
    """close_turn_async receives state only after finalize_turn_history."""
    _enable_session(monkeypatch, test_settings)
    seen_assistant_at_close = {"value": False}

    def _track_close(state):
        roles = [m.get("role") for m in (state.get("conversation_history") or [])]
        seen_assistant_at_close["value"] = "assistant" in roles

    monkeypatch.setattr("app.services.close_turn_async.close_turn_async", _track_close)
    _patch_reflect_and_eval_noop(monkeypatch)
    monkeypatch.setattr(
        "app.services.conversation_context.write_turn_memories",
        lambda state: state,
    )

    state1 = _completed_turn_one_state(session_id="order-1")
    finalized = GraphRunner()._finalize_turn(state1)

    assert seen_assistant_at_close["value"] is True
    history = finalized.get("conversation_history") or []
    assert any(m.get("role") == "assistant" for m in history)
