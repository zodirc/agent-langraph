"""
Live LLM end-to-end for engineering_mode (optional CI).

Run when model is configured:
  ENGINEERING_E2E_LIVE=1 pytest tests/e2e/test_engineering_mode_live.py -v

Requires MODEL_ENABLED and API credentials in environment / .env.
"""

from __future__ import annotations

import os

import pytest

from app.config.settings import settings
from app.runtime.state import TaskStatus
from app.services.graph_runner import GraphRunner


def _live_enabled() -> bool:
    return os.environ.get("ENGINEERING_E2E_LIVE", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(not _live_enabled(), reason="set ENGINEERING_E2E_LIVE=1"),
    pytest.mark.skipif(
        not getattr(settings, "MODEL_ENABLED", True),
        reason="MODEL_ENABLED=false",
    ),
]


@pytest.mark.timeout(300)
def test_live_engineering_2048_graph(isolated_stores):
    """Full graph with real planning + engineering path for interactive_app goal."""
    runner = GraphRunner()
    state = runner.start_task(
        user_id="e2e-live",
        task_type="qa",
        input_payload={
            "goal": "做一个简单的浏览器 2048 小游戏，落盘到 games 目录，包含 index.html 和 game.js",
            "risk_level": "LOW",
            "needs_search": False,
        },
        execution_mode="single",
    )
    payload = state.get("input_payload") or {}
    assert payload.get("target_mode") == "engineering_mode", payload.get("mode_resolution")
    trace = payload.get("engineering_trace") or {}
    assert len(trace.get("written_files") or []) >= 2, trace
    answer = str(state.get("final_answer") or "")
    assert "文件清单" in answer
    assert state["status"] in (
        TaskStatus.COMPLETED.value,
        TaskStatus.REASONED.value,
        TaskStatus.POLICY_CHECKED.value,
        TaskStatus.WAITING_REVIEW.value,
    )


@pytest.mark.timeout(180)
def test_live_qa_followup_after_engineering_hint(isolated_stores):
    """Session: engineering turn then QA follow-up should route to qa_mode on second task."""
    if not _live_enabled():
        pytest.skip("live off")
    runner = GraphRunner()
    session_id = "e2e-eng-qa-session"
    state1 = runner.start_task(
        user_id="e2e-live",
        task_type="qa",
        input_payload={
            "goal": "请直接生成一个最小的 HTML 点击计数器页面到 games/counter/",
            "risk_level": "LOW",
        },
        execution_mode="single",
        session_id=session_id,
        new_session=True,
    )
    assert (state1.get("input_payload") or {}).get("target_mode") == "engineering_mode"

    state2 = runner.start_task(
        user_id="e2e-live",
        task_type="qa",
        input_payload={
            "goal": "为什么刚才的实现用这种方式？简要解释原理即可，不要重新生成文件",
            "risk_level": "LOW",
        },
        execution_mode="single",
        session_id=session_id,
        new_session=False,
    )
    payload2 = state2.get("input_payload") or {}
    assert payload2.get("target_mode") == "qa_mode", payload2.get("mode_resolution")
    history = {str(h.get("node")) for h in (state2.get("node_history") or []) if isinstance(h, dict)}
    assert "engineering_execution" not in history
