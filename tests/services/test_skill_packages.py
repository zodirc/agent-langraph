import app.services.skill_registry as skill_mod
from app.domain.skill_models import SkillSourceType
from app.services.skill_hooks_bootstrap import bootstrap_skill_hooks
from app.services.skill_package_loader import load_all_package_definitions, list_installed_packages
from app.services.skill_registry import SkillRegistry, reset_skill_registry
from app.services.skill_resolver import resolve_skill_for_task


def test_load_enterprise_package_and_hook_overlay():
    reset_skill_registry()
    bootstrap_skill_hooks()
    registry = SkillRegistry()
    root = __import__("pathlib").Path(__file__).resolve().parents[2] / "config" / "skills"
    registry.load_from_config_dir(root)
    pkg_defs, _warns = load_all_package_definitions()
    assert any(d.skill_id == "security_hardened_review" for d in pkg_defs)
    for d in pkg_defs:
        registry.register(d)
    skill_mod._registry = registry
    defn, policy, _snap = resolve_skill_for_task("security_hardened_review", user_role="user")
    assert defn.source_type == SkillSourceType.SYSTEM_PACKAGE
    assert "OWASP" in policy.resolved_planning_overlay or "security" in policy.resolved_planning_overlay.lower()
    packages = list_installed_packages()
    assert any(p.get("package_id") == "demo_enterprise" for p in packages)
