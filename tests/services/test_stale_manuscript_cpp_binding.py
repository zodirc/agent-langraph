from app.services.artifact_resolver import resolve_artifact_target
from app.services.manuscript_service import Manuscript, enrich_payload, sanitize_manuscript_bindings


def test_sanitize_drops_cpp_body_path():
    out = sanitize_manuscript_bindings({"body_path": "main.cpp", "body_bytes": 100})
    assert "body_path" not in out


def test_resolve_body_create_when_cpp_bound(tmp_path, monkeypatch):
    from tests.conftest import patch_task_artifact_dir

    task_id = "cpp-bind"
    patch_task_artifact_dir(monkeypatch, tmp_path)
    ms = Manuscript(task_id=task_id, body_path="main.cpp")
    state = {
        "task_id": task_id,
        "input_payload": {"manuscript": ms.to_dict()},
        "mission": {"kind": "writing", "step_policy": {"body_artifact": "novel.txt"}},
    }
    target = resolve_artifact_target(state, action="write_body", target_hint="body", require_exists=False)
    assert target.filename == "novel.txt"


def test_enrich_payload_skips_cpp_binding():
    ms = Manuscript(task_id="t1", body_path="main.cpp")
    enriched = enrich_payload({"goal": "写代码"}, "t1", manuscript=ms)
    assert enriched == {"goal": "写代码"}
