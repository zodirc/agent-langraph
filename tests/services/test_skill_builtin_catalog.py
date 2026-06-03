"""§13 productized builtin skills — full catalog from config/skills."""

from pathlib import Path

from app.services.skill_registry import SkillRegistry, reset_skill_registry

BUILTIN_SKILL_IDS_DOC_13 = (
    "code_review",
    "bug_fix",
    "refactor_plan",
    "api_design",
    "doc_summarize",
    "spec_writer",
    "meeting_minutes",
    "knowledge_ingest",
    "research_brief",
    "root_cause_analysis",
    "decision_memo",
    "novel_outline",
    "chapter_writer",
    "outline_patch",
    "consistency_review",
)


def test_doc_13_builtin_catalog_complete():
    reset_skill_registry()
    registry = SkillRegistry()
    root = Path(__file__).resolve().parents[2] / "config" / "skills"
    count = registry.load_from_config_dir(root)
    assert count >= len(BUILTIN_SKILL_IDS_DOC_13)
    loaded = {s.skill_id for s in registry.list_definitions(role="user", include_disabled=True)}
    missing = [sid for sid in BUILTIN_SKILL_IDS_DOC_13 if sid not in loaded]
    assert not missing, f"missing builtins: {missing}"


def test_builtin_skills_have_input_form_schema():
    reset_skill_registry()
    registry = SkillRegistry()
    root = Path(__file__).resolve().parents[2] / "config" / "skills"
    registry.load_from_config_dir(root)
    for sid in BUILTIN_SKILL_IDS_DOC_13:
        defn = registry.load_definition(sid)
        assert registry.role_allows(defn.required_role, "user"), sid
        schema = defn.presentation.input_form_schema
        assert isinstance(schema, dict), sid
        assert schema.get("type") == "object", sid
        assert "goal" in (schema.get("properties") or {}), sid
