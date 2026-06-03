from app.config.prompt_templates import resolve_role_instructions
from app.domain.skill_models import SkillDefinition
from app.services.skill_runtime_policy import build_runtime_policy


def test_planning_prompt_includes_skill_overlay():
    defn = SkillDefinition(
        skill_id="test_overlay",
        name="Test",
        version="1.0.0",
        planning_overlay="SKILL_OVERLAY_MARKER: prioritize security.",
    )
    policy = build_runtime_policy(defn)
    state = {"skill_runtime_policy": policy.to_dict(), "task_type": "qa", "input_payload": {}}
    role = resolve_role_instructions("planning", state=state)
    assert "SKILL_OVERLAY_MARKER" in role
    assert "[Skill overlay]" in role


def test_no_overlay_without_policy():
    role = resolve_role_instructions("planning", state={"task_type": "qa", "input_payload": {}})
    assert "[Skill overlay]" not in role
