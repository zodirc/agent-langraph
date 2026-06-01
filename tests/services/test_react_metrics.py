"""Prometheus metrics for SRDL (react loop)."""

from __future__ import annotations

from app.services.metrics_service import MetricsService
from app.services.react_audit import export_react_loop_prometheus, react_metrics_from_state
from app.runtime.state import create_initial_state, merge_state
from app.services.react_entry import init_react_loop_state
from app.services.react_loop_runner import finalize_loop


def test_record_react_loop_outcome_increments_counters():
    from prometheus_client import CollectorRegistry

    svc = MetricsService(registry=CollectorRegistry())
    svc.record_react_loop_outcome(
        status="finished",
        exit_path="finish_with_answer",
        step_count=3,
        action_distribution={"retrieve_knowledge": 1, "reason": 1, "finish": 1},
        replan_count=0,
    )
    summary = svc.summary()["counters"]
    assert summary["react_loop_finished"] == 1


def test_export_react_loop_prometheus_from_state(test_settings, monkeypatch):
    import app.config.settings as settings_mod

    test_settings.METRICS_ENABLED = True
    monkeypatch.setattr(settings_mod, "settings", test_settings)
    state = create_initial_state(
        task_id="metrics-react-1",
        input_payload={"goal": "test", "risk_level": "LOW"},
    )
    state = merge_state(state, react_loop=init_react_loop_state(state))
    state = finalize_loop(state, reason="enough_information", exit_path="finish_with_answer")
    from prometheus_client import CollectorRegistry
    import app.services.metrics_service as metrics_mod

    metrics_mod._service = MetricsService(registry=CollectorRegistry())
    export_react_loop_prometheus(state)
    metrics = react_metrics_from_state(state)
    assert metrics["loop_status"] == "finished"
