from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.skill_registry import reset_skill_registry
from app.services.skill_store import reset_skill_store


@pytest.fixture
def client(isolated_stores, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reset_skill_registry()
    reset_skill_store()
    data_dir = tmp_path / "skills_data"
    import app.config.settings as settings_mod
    import app.services.skill_governance as gov_mod

    monkeypatch.setattr(settings_mod.settings, "SKILL_DATA_DIR", str(data_dir))
    monkeypatch.setattr(gov_mod.settings, "SKILL_DATA_DIR", str(data_dir))
    import app.services.skill_registry as reg_mod
    from app.services.skill_hooks_bootstrap import bootstrap_skill_hooks
    from app.services.skill_registry import SkillRegistry

    bootstrap_skill_hooks()
    reg = SkillRegistry()
    reg.load_from_config_dir(Path(__file__).resolve().parents[2] / "config" / "skills")
    reg.load_skill_packages()
    reg_mod._registry = reg
    return TestClient(app)


def test_marketplace_packages(client: TestClient):
    res = client.get("/skills/marketplace/packages", headers={"X-User-Role": "user"})
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 1
    assert any(p.get("package_id") == "demo_enterprise" for p in data["packages"])


def test_admin_governance(client: TestClient):
    headers = {"X-User-Role": "admin", "X-Tenant-Id": "acme"}
    data_dir = Path(__file__).resolve().parents[2] / "tests" / "services"
    put = client.put(
        "/skills/admin/global-disabled",
        headers=headers,
        json={"skill_ids": ["echo_helper"], "reason": "test"},
    )
    assert put.status_code == 200
    snap = client.get("/skills/admin/governance", headers=headers)
    assert snap.status_code == 200
    assert "echo_helper" in snap.json()["global_disabled"]
