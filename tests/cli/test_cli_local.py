import json
import subprocess
import sys


def test_cli_local_run():
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "--local", "run", "hello from cli"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[2]),
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "task_id" in payload
    assert payload["status"] in (
        "COMPLETED",
        "REASONED",
        "POLICY_CHECKED",
        "WAITING_REVIEW",
        "DEAD_LETTER",
    )
