import shutil
from unittest.mock import patch

import pytest

from app.services.code_verify.config import load_verify_config, resolve_backend_id
from app.services.code_verify.models import VerifyResult
from app.services.code_verify.pipeline import verify_code_artifacts, verify_reports_all_ok
from app.services.code_verify.runner import expand_cmd, run_command
from app.services.code_verify.backends import verify_source


VALID_CPP = """#include <iostream>
int main() {
    std::cout << "ok" << std::endl;
    return 0;
}
"""

INVALID_CPP = """#include <iostream>
int main( {
    return 0;
}
"""


def test_expand_cmd_substitutes_file():
    cmd = expand_cmd(["g++", "-c", "{file}"], file_path="/tmp/x.cpp")
    assert cmd == ["g++", "-c", "/tmp/x.cpp"]


def test_resolve_backend_id():
    assert resolve_backend_id("cpp") == "cpp"
    assert resolve_backend_id("python") == "python"


def test_verify_reports_all_ok():
    assert verify_reports_all_ok(
        [VerifyResult(ok=True, backend="cpp", stage="compile", language="cpp")]
    )
    assert not verify_reports_all_ok(
        [VerifyResult(ok=False, backend="cpp", stage="compile", language="cpp")]
    )


@patch("app.services.code_verify.runner.subprocess.run")
def test_run_command_success(mock_run):
    mock_run.return_value = type(
        "R", (), {"returncode": 0, "stdout": "ok", "stderr": ""}
    )()
    code, out, err, ms = run_command(["true"], timeout_sec=5)
    assert code == 0
    assert ms >= 0


@pytest.mark.skipif(not shutil.which("g++"), reason="g++ not installed")
def test_verify_valid_cpp_compiles():
    result = verify_source(VALID_CPP, language="cpp", backend_id="cpp", task_id="t-verify-ok")
    assert result.ok is True
    assert result.stage == "compile"


@pytest.mark.skipif(not shutil.which("g++"), reason="g++ not installed")
def test_verify_invalid_cpp_fails():
    result = verify_source(INVALID_CPP, language="cpp", backend_id="cpp", task_id="t-verify-bad")
    assert result.ok is False
    assert result.stage == "compile"
    assert result.stderr or result.issues


@pytest.mark.skipif(not shutil.which("python3"), reason="python3 not installed")
def test_verify_valid_python_compiles():
    py_code = "def f():\n    return 1\n"
    result = verify_source(py_code, language="python", backend_id="python", task_id="t-py-ok")
    assert result.ok is True


def test_verify_code_artifacts_skipped_when_disabled(monkeypatch):
    monkeypatch.setattr(
        "app.services.code_verify.pipeline.load_verify_config",
        lambda: type(
            "C",
            (),
            {"enabled": False},
        )(),
    )
    assert verify_code_artifacts([{"kind": "code", "content": "x", "language": "cpp"}]) == []


def test_load_verify_config_has_defaults():
    cfg = load_verify_config()
    assert "cpp" in cfg.backends or cfg.enabled is False
