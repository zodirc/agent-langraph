from pathlib import Path

from app.domain.skill import SkillManifest
from app.services.skill_registry import SkillRegistry, reset_skill_registry


def test_register_and_list_skills():
    registry = SkillRegistry()
    registry.register_manifest(
        SkillManifest(
            skill_id="test_skill",
            name="Test",
            version="1.0.0",
            description="demo",
        )
    )
    assert "test_skill" in [s.skill_id for s in registry.list_skills()]


def test_disclosure_filters_by_role():
    registry = SkillRegistry()
    registry.register_manifest(
        SkillManifest(
            skill_id="admin_skill",
            name="Admin",
            version="1.0.0",
            description="",
            required_role="admin",
        )
    )
    user_ids = registry.get_disclosure({"user_role": "user"})
    assert "admin_skill" not in user_ids
    admin_ids = registry.get_disclosure({"user_role": "admin"})
    assert "admin_skill" in admin_ids


def test_load_from_config_dir():
    reset_skill_registry()
    registry = SkillRegistry()
    root = Path(__file__).resolve().parents[2] / "config" / "skills"
    count = registry.load_from_config_dir(root)
    assert count >= 16
    ids = [s.skill_id for s in registry.list_skills()]
    assert "echo_helper" in ids
    assert "code_review" in ids
