from pathlib import Path

from app.services import knowledge_paths


def test_default_knowledge_dir_is_repo_knowledge():
    root = knowledge_paths.repo_root()
    assert knowledge_paths.knowledge_content_dir() == root / "knowledge"
    assert knowledge_paths.writing_guidelines_path().is_file()


def test_knowledge_content_dir_env_override(monkeypatch, tmp_path: Path):
    custom = tmp_path / "custom_kb"
    custom.mkdir()
    (custom / "writing_guidelines.md").write_text("# stub", encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGE_CONTENT_DIR", str(custom))
    assert knowledge_paths.knowledge_content_dir() == custom
    assert knowledge_paths.writing_guidelines_path() == custom / "writing_guidelines.md"
