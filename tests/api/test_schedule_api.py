from fastapi.testclient import TestClient

from app.main import app


def test_create_and_list_schedule(isolated_stores):
    client = TestClient(app)
    created = client.post(
        "/schedules",
        json={
            "name": "hourly-check",
            "cron_expression": "0 * * * *",
            "input_payload": {"goal": "scheduled health check"},
        },
    )
    assert created.status_code == 201
    schedule_id = created.json()["schedule_id"]

    listed = client.get("/schedules")
    assert listed.status_code == 200
    assert any(s["schedule_id"] == schedule_id for s in listed.json()["schedules"])

    deleted = client.delete(
        f"/schedules/{schedule_id}",
        headers={"X-User-Role": "admin"},
    )
    assert deleted.status_code == 200
