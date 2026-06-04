from pathlib import Path

import pytest

from app.services.artifact_tools import task_artifact_dir
from app.services.project_verify.backends import verify_project
from app.services.session_fs_tools import handle_mkdir_path, handle_write_file


def test_web_html_js_structure_check(tmp_path, monkeypatch):
    task_id = "pv-web-1"
    root = task_artifact_dir(task_id)
    root.mkdir(parents=True, exist_ok=True)
    base = "games/demo"
    handle_mkdir_path({"task_id": task_id, "path": base, "parents": True, "exist_ok": True})
    handle_write_file(
        {
            "task_id": task_id,
            "path": f"{base}/index.html",
            "content": "<!DOCTYPE html><html><body></body></html>",
            "parents": True,
        }
    )
    handle_write_file(
        {
            "task_id": task_id,
            "path": f"{base}/game.js",
            "content": "const x = 1;\n",
            "parents": True,
        }
    )
    result = verify_project(task_id, intent_kind="interactive_app", backend_id="web_html_js")
    if result.status == "failed" and "node" in " ".join(result.issues).lower():
        pytest.skip("node not available in CI")
    assert result.backend == "web_html_js"
    assert result.ok or result.stage == "syntax_check"


def test_path_traversal_rejected():
    from app.services.session_fs_tools import _resolve_in_session

    with pytest.raises(ValueError, match="escapes"):
        _resolve_in_session("t1", "../etc/passwd")
