from pathlib import Path

import pytest

from app.services.skill_governance import (
    get_governance_snapshot,
    is_skill_blocked_for_tenant,
    set_global_disabled,
    set_tenant_blocks,
)
from app.services.skill_resolver import SkillNotAvailableError, resolve_skill_for_task
import app.services.skill_registry as skill_mod
from app.services.skill_registry import SkillRegistry, reset_skill_registry


@pytest.fixture
def governance_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reset_skill_registry()
    data = tmp_path / "skills_data"
    import app.config.settings as settings_mod
    import app.services.skill_governance as gov_mod

    monkeypatch.setattr(settings_mod.settings, "SKILL_DATA_DIR", str(data))
    monkeypatch.setattr(gov_mod.settings, "SKILL_DATA_DIR", str(data))
    registry = SkillRegistry()
    root = Path(__file__).resolve().parents[2] / "config" / "skills"
    registry.load_from_config_dir(root)
    skill_mod._registry = registry
    yield data
    reset_skill_registry()


def test_global_disable_blocks_resolve(governance_env):
    set_global_disabled(["qa_general"], reason="test", operator="admin")
    assert is_skill_blocked_for_tenant("qa_general", "acme")
    with pytest.raises(SkillNotAvailableError):
        resolve_skill_for_task("qa_general", user_role="user", tenant_id="acme")


def test_tenant_block_only_affects_tenant(governance_env):
    set_global_disabled([], operator="admin")
    set_tenant_blocks("acme", ["code_review"], operator="admin")
    assert is_skill_blocked_for_tenant("code_review", "acme")
    assert not is_skill_blocked_for_tenant("code_review", "other")
    snap = get_governance_snapshot()
    assert "code_review" in snap["tenant_blocks"].get("acme", [])
