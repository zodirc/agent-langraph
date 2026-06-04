"""Stale sessions: engineering files must not bind as text-artifact manuscript paths."""

from pathlib import Path

from app.services.manuscript_service import (
    Manuscript,
    enrich_payload,
    resolve_body_filename,
    resolve_manuscript,
    sanitize_manuscript_bindings,
)
from app.services.session.turn_policy import apply_qa_turn_isolation


def test_sanitize_manuscript_bindings_drops_cpp():
    out = sanitize_manuscript_bindings(
        {"body_path": "main.cpp", "body_bytes": 120, "outline_path": "outline.txt"}
    )
    assert "body_path" not in out
    assert out.get("outline_path") == "outline.txt"


def test_resolve_manuscript_ignores_stored_cpp_body(
    base_state, test_settings, monkeypatch
):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = base_state["task_id"]
    art_dir = Path(test_settings.ARTIFACTS_PATH) / task_id
    art_dir.mkdir(parents=True, exist_ok=True)
    (art_dir / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (art_dir / "novel.txt").write_text("hello", encoding="utf-8")

    ms = resolve_manuscript(
        task_id,
        {"body_path": "main.cpp", "body_bytes": 40},
    )
    assert ms.body_path == "novel.txt"


def test_resolve_body_filename_falls_back_when_bound_cpp():
    ms = Manuscript(task_id="t", body_path="main.cpp")
    name = resolve_body_filename(manuscript=ms, payload={}, intent={})
    assert name.endswith(".txt")


def test_enrich_payload_hello_with_stored_cpp_no_extension_error(
    base_state, test_settings, monkeypatch
):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = base_state["task_id"]
    art_dir = Path(test_settings.ARTIFACTS_PATH) / task_id
    art_dir.mkdir(parents=True, exist_ok=True)
    (art_dir / "main.cpp").write_text("int main() {}\n", encoding="utf-8")

    ms = resolve_manuscript(task_id, {"body_path": "main.cpp", "body_bytes": 12})
    enriched = enrich_payload({"goal": "你好"}, task_id, manuscript=ms)
    assert "previous_artifact_excerpt" not in enriched or enriched.get("novel_filename")


def test_apply_qa_turn_isolation_clears_cpp_manuscript():
    existing = {
        "mission": {"kind": "writing", "id": "m1"},
        "manuscript": {"body_path": "main.cpp", "body_bytes": 99},
    }
    out = apply_qa_turn_isolation({"goal": "你好"}, existing)
    assert out.get("manuscript", {}).get("body_path") is None
