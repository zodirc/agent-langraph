"""Per-language compile/run backends (config-driven command templates)."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from app.services.code_verify.config import BackendConfig, VerifyConfig, load_verify_config
from app.services.code_verify.models import VerifyResult
from app.services.code_verify.runner import expand_cmd, run_command


def _workspace_dir(cfg: VerifyConfig, task_id: str) -> Path:
    root = Path(cfg.workspace_root)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in task_id)[:64]
    path = root / safe / uuid.uuid4().hex[:12]
    path.mkdir(parents=True, exist_ok=True)
    return path


def verify_source(
    content: str,
    *,
    language: str,
    backend_id: str | None = None,
    task_id: str = "verify",
    filename: str = "",
) -> VerifyResult:
    cfg = load_verify_config()
    if not cfg.enabled:
        return VerifyResult(
            ok=True,
            backend="",
            stage="skipped",
            language=language,
            skipped_reason="verify_disabled",
        )

    from app.services.code_verify.config import resolve_backend_id

    bid = backend_id or resolve_backend_id(language, filename=filename)
    if not bid:
        return VerifyResult(
            ok=True,
            backend="",
            stage="skipped",
            language=language,
            skipped_reason=f"no_backend_for_language={language}",
        )

    backend = get_backend(bid)
    if backend is None or not backend.enabled:
        return VerifyResult(
            ok=True,
            backend=bid,
            stage="skipped",
            language=language,
            skipped_reason="backend_disabled",
        )

    if not backend.compile_cmd:
        return VerifyResult(
            ok=True,
            backend=bid,
            stage="skipped",
            language=language,
            skipped_reason="no_compile_cmd",
        )

    ext = backend.source_extension
    if filename and "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1]

    work = _workspace_dir(cfg, task_id)
    source_name = f"main{ext}"
    source_path = (work / source_name).resolve()
    source_path.write_text(content, encoding="utf-8")

    compile_cmd = expand_cmd(backend.compile_cmd, file_path=str(source_path))
    code, stdout, stderr, duration_ms = run_command(
        compile_cmd,
        timeout_sec=cfg.timeout_sec,
        cwd=str(work),
    )

    if code != 0:
        return VerifyResult(
            ok=False,
            backend=bid,
            stage="compile",
            language=language,
            exit_code=code,
            duration_ms=duration_ms,
            stdout=stdout,
            stderr=stderr,
            source_path=str(source_path),
            issues=[f"compile_failed exit={code}"],
        )

    result = VerifyResult(
        ok=True,
        backend=bid,
        stage="compile",
        language=language,
        exit_code=code,
        duration_ms=duration_ms,
        stdout=stdout,
        stderr=stderr,
        source_path=str(source_path),
    )

    if backend.run_cmd:
        binary = work / "a.out"
        if bid == "cpp" and not binary.exists():
            # g++ -c does not produce binary; optional link not configured
            return result
        run_cmd = expand_cmd(
            backend.run_cmd,
            file_path=str(source_path),
            binary_path=str(binary) if binary.exists() else str(source_path),
        )
        rcode, rout, rerr, rms = run_command(
            run_cmd,
            timeout_sec=cfg.timeout_sec,
            cwd=str(work),
        )
        result = VerifyResult(
            ok=rcode == 0,
            backend=bid,
            stage="run",
            language=language,
            exit_code=rcode,
            duration_ms=result.duration_ms + rms,
            stdout=(result.stdout + "\n" + rout).strip(),
            stderr=(result.stderr + "\n" + rerr).strip(),
            source_path=str(source_path),
            issues=[] if rcode == 0 else [f"run_failed exit={rcode}"],
        )

    try:
        for child in work.iterdir():
            if child.is_file():
                child.unlink(missing_ok=True)
        work.rmdir()
        if work.parent.exists() and not any(work.parent.iterdir()):
            work.parent.rmdir()
    except OSError:
        pass

    return result


def default_backends_if_missing() -> dict[str, BackendConfig]:
    """Built-in defaults when config omits backends (g++/python3 on PATH)."""
    return {
        "cpp": BackendConfig(
            id="cpp",
            enabled=True,
            compile_cmd=("g++", "-std=c++17", "-Wall", "-Wextra", "-c", "{file}"),
            source_extension=".cpp",
        ),
        "python": BackendConfig(
            id="python",
            enabled=True,
            compile_cmd=("python3", "-m", "py_compile", "{file}"),
            source_extension=".py",
        ),
    }


def get_backend(bid: str) -> BackendConfig | None:
    cfg = load_verify_config()
    backend = cfg.backends.get(bid)
    if backend:
        return backend
    return default_backends_if_missing().get(bid)
