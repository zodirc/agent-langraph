import json
import subprocess
import sys


def _last_json_object(stdout: str) -> dict:
    decoder = json.JSONDecoder()
    text = stdout.strip()
    payload: dict = {}
    idx = 0
    while idx < len(text):
        while idx < len(text) and text[idx].isspace():
            idx += 1
        if idx >= len(text):
            break
        payload, idx = decoder.raw_decode(text, idx)
    return payload


def test_cli_local_run():
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "--local", "run", "hello from cli"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[2]),
        env={**__import__("os").environ, "HF_HUB_OFFLINE": "1"},
    )
    assert result.returncode == 0, result.stderr
    payload = _last_json_object(result.stdout)
    assert "task_id" in payload
    assert payload["status"] in (
        "COMPLETED",
        "REASONED",
        "POLICY_CHECKED",
        "WAITING_REVIEW",
        "DEAD_LETTER",
    )
