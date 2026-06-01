"""Integration: SRDL path through single graph when react_loop.enabled."""

from __future__ import annotations

from app.config.settings import Settings
from app.runtime.state import TaskStatus
from app.services.graph_runner import GraphRunner


def test_srdl_graph_completes_with_force_react(
    isolated_stores, test_settings: Settings, monkeypatch
):
    test_settings.REACT_LOOP_ENABLED = True
    for mod in (
        "app.services.react_entry",
        "app.runtime.router",
        "app.services.react_loop_runner",
        "app.services.graph_runner",
    ):
        monkeypatch.setattr(f"{mod}.settings", test_settings)
    runner = GraphRunner()
    state = runner.start_task(
        user_id="tester",
        task_type="qa",
        input_payload={
            "goal": "分析并对比 LangGraph agent 架构模式",
            "risk_level": "LOW",
            "force_react_loop": True,
            "needs_search": True,
            "selected_tools": ["get_runtime_info"],
        },
    )
    audit_nodes = [a.get("node") for a in (state.get("audit_log") or [])]
    assert any(n and str(n).startswith("react_") for n in audit_nodes)

    loop = state.get("react_loop") or {}
    assert loop.get("status") in ("finished", "aborted") or len(loop.get("history") or []) > 0

    assert state["status"] in (
        TaskStatus.COMPLETED.value,
        TaskStatus.WAITING_REVIEW.value,
        TaskStatus.REASONED.value,
    )
