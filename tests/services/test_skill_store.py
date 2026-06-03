from pathlib import Path

import pytest

from app.domain.skill_models import SkillDefinition, SkillStatus
from app.services.skill_registry import SkillRegistry, reset_skill_registry
from app.services.skill_store import SkillStore, reset_skill_store


@pytest.fixture
def skill_store_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reset_skill_store()
    reset_skill_registry()
    data_dir = tmp_path / "skills_data"
    monkeypatch.setattr(
        "app.services.skill_store.settings.SKILL_DATA_DIR",
        str(data_dir),
        raising=False,
    )
    import app.config.settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "SKILL_DATA_DIR", str(data_dir))
    yield SkillStore(), data_dir
    reset_skill_store()


def test_save_publish_and_list(skill_store_tmp):
    store, _data = skill_store_tmp
    defn = SkillDefinition(
        skill_id="tenant_test_skill",
        name="Tenant Test",
        version="1.0.0",
        description="demo",
        allowed_tools=[],
    )
    store.save_draft(defn, tenant_id="acme", operator="u1")
    listed = store.list_custom("acme", status=SkillStatus.DRAFT)
    assert len(listed) == 1

    published = store.publish("tenant_test_skill", tenant_id="acme", operator="u1")
    assert published.status == SkillStatus.PUBLISHED
    versions = store.list_versions("tenant_test_skill", "acme")
    assert len(versions) >= 1


def test_clone_from_builtin(skill_store_tmp):
    store, _data = skill_store_tmp
    registry = SkillRegistry()
    root = Path(__file__).resolve().parents[2] / "config" / "skills"
    registry.load_from_config_dir(root)
    source = registry.load_definition("qa_general")
    clone = store.clone_from(
        source,
        new_skill_id="qa_general_copy",
        tenant_id="acme",
        operator="u1",
        owner_id="acme",
    )
    assert clone.skill_id == "qa_general_copy"
    assert clone.status == SkillStatus.DRAFT
    loaded = store.load("qa_general_copy", "acme")
    assert loaded is not None
    assert "qa_general" in (loaded.planning_overlay or loaded.description or loaded.name)
