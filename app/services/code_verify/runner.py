"""Subprocess execution with timeout for compile/run commands."""

from __future__ import annotations

import subprocess
import time
from typing import Sequence


def run_command(
    cmd: Sequence[str],
    *,
    timeout_sec: int,
    cwd: str | None = None,
) -> tuple[int, str, str, int]:
    """Run command; return (exit_code, stdout, stderr, duration_ms)."""
    if not cmd:
        return 1, "", "empty command", 0
    start = time.monotonic()
    try:
        proc = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            timeout=max(1, timeout_sec),
            cwd=cwd,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        return proc.returncode, proc.stdout or "", proc.stderr or "", duration_ms
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        err = f"timeout after {timeout_sec}s"
        if exc.stdout:
            err += f"\nstdout: {exc.stdout[:2000]}"
        if exc.stderr:
            err += f"\nstderr: {exc.stderr[:2000]}"
        return 124, "", err, duration_ms
    except FileNotFoundError as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return 127, "", str(exc), duration_ms
    except OSError as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return 1, "", str(exc), duration_ms


def expand_cmd(template: Sequence[str], *, file_path: str, binary_path: str = "") -> list[str]:
    out: list[str] = []
    for part in template:
        out.append(
            str(part)
            .replace("{file}", file_path)
            .replace("{binary}", binary_path)
        )
    return out
