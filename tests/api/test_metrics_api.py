from fastapi.testclient import TestClient

from app.main import app
from app.services.metrics_service import get_metrics_service


def test_metrics_summary(isolated_stores):
    get_metrics_service().inc_task_created()
    client = TestClient(app)
    response = client.get("/metrics/summary")
    assert response.status_code == 200
    body = response.json()
    assert "counters" in body
    assert body["counters"]["tasks_created"] >= 1
