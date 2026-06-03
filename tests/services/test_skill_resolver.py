import pytest

from app.domain.skill_models import SkillDefinition, SkillStatus
from app.services.skill_registry import SkillRegistry, reset_skill_registry
from app.services.skill_resolver import (
    SkillNotFoundError,
    attach_skill_to_payload,
    resolve_skill_for_task,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_skill_registry()
    yield
    reset_skill_registry()


def test_resolve_builtin_code_review():
    import app.services.skill_registry as skill_mod

    root = __import__("pathlib").Path(__file__).resolve().parents[2] / "config" / "skills"
    registry = SkillRegistry()
    assert registry.load_from_config_dir(root) >= 4
    skill_mod._registry = registry
    assert len(registry.list_definitions(role="user")) >= 4
    definition, policy, snapshot = resolve_skill_for_task("code_review", user_role="user")
    assert definition.skill_id == "code_review"
    assert policy.resolved_domain == "code"
    assert "code_review" in policy.resolved_planning_overlay.lower()
    assert snapshot["skill_id"] == "code_review"


def test_attach_skill_to_payload_sets_internal_policy():
    import app.services.skill_registry as skill_mod

    root = __import__("pathlib").Path(__file__).resolve().parents[2] / "config" / "skills"
    registry = SkillRegistry()
    registry.load_from_config_dir(root)
    skill_mod._registry = registry
    out = attach_skill_to_payload(
        {"goal": "review main.py"},
        skill_id="qa_general",
        skill_params={"goal": "review main.py"},
        user_role="user",
    )
    assert out["skill_id"] == "qa_general"
    assert "_skill_policy" in out
    assert out["_skill_policy"]["skill_id"] == "qa_general"


def test_unknown_skill_raises():
    registry = SkillRegistry()
    with pytest.raises(SkillNotFoundError):
        resolve_skill_for_task("missing_skill_xyz")


def test_draft_skill_not_available():
    import app.services.skill_registry as skill_mod

    from app.services.skill_resolver import SkillNotAvailableError

    registry = SkillRegistry()
    registry.register(
        SkillDefinition(
            skill_id="draft_only",
            name="Draft",
            version="1.0.0",
            status=SkillStatus.DRAFT,
        )
    )
    skill_mod._registry = registry
    with pytest.raises(SkillNotAvailableError):
        resolve_skill_for_task("draft_only", user_role="user")
