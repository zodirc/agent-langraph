from unittest.mock import patch

import pytest

from app.services.project_verify.backends import verify_project
from app.services.verify_backend_tool import handle_verify_backend


def test_illegal_backend_id_via_tool():
    out = handle_verify_backend(
        {"task_id": "t-sec-1", "backend_id": "bash -c evil", "intent_kind": "code"}
    )
    assert out["status"] == "error"
    assert "backend_not_whitelisted" in (out.get("issues") or [])


def test_verify_project_rejects_unknown_backend():
    result = verify_project("t-sec-2", intent_kind="code", backend_id="run_shell")
    assert not result.ok
    assert "backend_not_whitelisted" in result.issues


@patch("app.services.code_verify.backends.run_command")
def test_verify_timeout_surfaces(mock_run):
    mock_run.return_value = (124, "", "timeout after 1s", 1000)
    from app.services.artifact_tools import task_artifact_dir
    from app.services.session_fs_tools import handle_write_file

    task_id = "t-sec-timeout"
    root = task_artifact_dir(task_id)
    root.mkdir(parents=True, exist_ok=True)
    handle_write_file(
        {
            "task_id": task_id,
            "path": "main.cpp",
            "content": "int main() { return 0; }",
            "parents": True,
        }
    )
    result = verify_project(task_id, intent_kind="code", backend_id="cpp")
    assert not result.ok or result.status == "failed"


def test_command_injection_entry_path_rejected():
    from pathlib import Path
    import tempfile
    from unittest.mock import patch

    from app.services.project_verify.backends import _verify_web_html_js
    from app.services.project_verify.config import ProjectBackendConfig

    backend = ProjectBackendConfig(
        id="web_html_js",
        commands={"syntax_check": ("node", "--check", "{entry_js}")},
        required_files=frozenset(["index.html"]),
        entry_js_names=frozenset({"game.js"}),
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "index.html").write_text("<!DOCTYPE html><html></html>", encoding="utf-8")
        injected = root / "game.js"
        injected.write_text("x=1", encoding="utf-8")
        with patch(
            "app.services.project_verify.backends._find_entry_js",
            return_value=Path(str(injected) + ";rm -rf /"),
        ):
            result = _verify_web_html_js(root, backend, timeout_sec=5)
        assert not result.ok
        assert "entry_path_rejected" in result.issues


@patch("app.services.project_verify.backends._verify_slot")
def test_concurrency_limit_returns_degraded(mock_slot):
    class _Busy:
        def acquire(self, blocking=True):
            return False

        def release(self):
            pass

    mock_slot.return_value = _Busy()
    result = verify_project("t-sec-conc", intent_kind="interactive_app", backend_id="web_html_js")
    assert result.status == "degraded"
    assert "verify_concurrency_limit" in result.issues
