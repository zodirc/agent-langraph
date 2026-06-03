import app.services.skill_registry as skill_mod
from app.domain.skill_models import SkillDefinition, SkillStatus
from app.services.skill_registry import SkillRegistry, reset_skill_registry
from app.services.skill_dry_run import dry_run_skill


def test_dry_run_returns_policy_and_tools():
    reset_skill_registry()
    registry = SkillRegistry()
    root = __import__("pathlib").Path(__file__).resolve().parents[2] / "config" / "skills"
    registry.load_from_config_dir(root)
    skill_mod._registry = registry
    out = dry_run_skill("qa_general", goal="Explain tool selection", user_role="user")
    assert out["skill_id"] == "qa_general"
    assert out["runtime_policy"]["skill_id"] == "qa_general"
    assert "planning_prompt_preview" in out
    assert isinstance(out["visible_tools"], list)
