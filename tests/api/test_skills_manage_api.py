from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.skill_registry import reset_skill_registry
from app.services.skill_store import reset_skill_store


@pytest.fixture
def manage_client(isolated_stores, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reset_skill_registry()
    reset_skill_store()
    data_dir = tmp_path / "skills_data"
    import app.config.settings as settings_mod
    import app.services.skill_store as store_mod

    monkeypatch.setattr(settings_mod.settings, "SKILL_DATA_DIR", str(data_dir))
    monkeypatch.setattr(store_mod.settings, "SKILL_DATA_DIR", str(data_dir))
    reg = __import__("app.services.skill_registry", fromlist=["SkillRegistry"]).SkillRegistry()
    reg.load_from_config_dir(Path(__file__).resolve().parents[2] / "config" / "skills")
    import app.services.skill_registry as reg_mod

    reg_mod._registry = reg
    return TestClient(app)


def test_create_publish_clone_flow(manage_client: TestClient):
    client = manage_client
    headers = {"X-User-Role": "admin", "X-Tenant-Id": "acme"}

    create = client.post(
        "/skills",
        headers=headers,
        json={
            "skill_id": "custom_review",
            "name": "Custom Review",
            "description": "tenant skill",
            "allowed_tools": [],
            "planning_overlay": "Custom overlay marker",
        },
    )
    assert create.status_code == 201, create.text
    skill_id = create.json()["skill"]["skill_id"]
    assert skill_id == "custom_review"

    pub = client.post(
        f"/skills/{skill_id}/publish",
        headers=headers,
        json={"change_log": "first publish"},
    )
    assert pub.status_code == 200, pub.text
    assert pub.json()["skill"]["status"] == "published"

    listed = client.get("/skills?scope=tenant&status=published", headers=headers)
    assert any(s["skill_id"] == skill_id for s in listed.json()["skills"])

    clone = client.post(
        "/skills/code_review/clone",
        headers=headers,
        json={"new_skill_id": "code_review_fork"},
    )
    assert clone.status_code == 200, clone.text
    assert clone.json()["skill"]["skill_id"] == "code_review_fork"


def test_archive_custom_skill(manage_client: TestClient):
    client = manage_client
    headers = {"X-User-Role": "admin", "X-Tenant-Id": "acme2"}
    create = client.post(
        "/skills",
        headers=headers,
        json={
            "skill_id": "to_archive",
            "name": "Archive Me",
            "description": "demo",
            "allowed_tools": [],
        },
    )
    assert create.status_code == 201
    skill_id = create.json()["skill"]["skill_id"]
    archived = client.post(f"/skills/{skill_id}/archive", headers=headers)
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "archived"


def test_tenant_catalog_endpoint(manage_client: TestClient):
    client = manage_client
    headers = {"X-User-Role": "admin", "X-Tenant-Id": "catalog_tenant"}
    client.post(
        "/skills",
        headers=headers,
        json={
            "skill_id": "catalog_skill",
            "name": "Catalog",
            "description": "x",
            "allowed_tools": [],
        },
    )
    client.post(f"/skills/catalog_skill/publish", headers=headers, json={})
    cat = client.get("/skills/catalog/tenant", headers=headers)
    assert cat.status_code == 200, cat.text
    ids = [s["skill_id"] for s in cat.json()["skills"]]
    assert "catalog_skill" in ids
