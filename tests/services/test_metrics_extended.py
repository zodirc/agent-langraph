from app.services.metrics_service import MetricsService


def test_metrics_success_and_retry_rate():
    svc = MetricsService()
    svc.inc_task_created()
    svc.inc_task_created()
    svc.inc_task_completed(retry_count=1)
    svc.inc_task_failed()
    summary = svc.summary()
    assert summary["task_success_rate"] == 0.5
    assert summary["retry_rate"] == 1.0
    assert summary["counters"]["tasks_with_retry"] == 1
