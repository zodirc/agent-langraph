from fastapi.testclient import TestClient

from app.main import app
from app.services.skill_registry import reset_skill_registry


def test_list_skills_catalog(isolated_stores):
    reset_skill_registry()
    client = TestClient(app)
    res = client.get("/skills?status=published")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 4
    ids = {s["skill_id"] for s in data["skills"]}
    assert "code_review" in ids
    assert "qa_general" in ids


def test_dry_run_skill(isolated_stores):
    reset_skill_registry()
    client = TestClient(app)
    res = client.post(
        "/skills/qa_general/dry-run",
        headers={"X-User-Role": "user"},
        json={"goal": "How does planning work?"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["skill_id"] == "qa_general"
    assert "runtime_policy" in data
    assert "planning_prompt_preview" in data


def test_get_skill_detail(isolated_stores):
    reset_skill_registry()
    client = TestClient(app)
    res = client.get("/skills/code_review")
    assert res.status_code == 200
    body = res.json()
    assert body["definition"]["skill_id"] == "code_review"
    assert "allowed" in body["tools"]
