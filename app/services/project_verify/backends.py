"""Execute whitelisted project verify backends."""

from __future__ import annotations

import re
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.artifact_tools import task_artifact_dir
from app.services.code_verify.backends import verify_source
from app.services.code_verify.config import resolve_backend_id
from app.services.code_verify.models import VerifyResult
from app.services.code_verify.runner import expand_cmd, run_command
from app.services.project_verify.config import (
    ProjectBackendConfig,
    allowed_backend_ids,
    load_project_verify_config,
)

_verify_semaphore: threading.BoundedSemaphore | None = None
_verify_semaphore_size: int = 0


def _verify_slot():
    """Limit concurrent verify runs (§11.4 resource boundary)."""
    global _verify_semaphore, _verify_semaphore_size
    cfg = load_project_verify_config()
    size = max(1, cfg.max_concurrent_verifies)
    if _verify_semaphore is None or _verify_semaphore_size != size:
        _verify_semaphore = threading.BoundedSemaphore(size)
        _verify_semaphore_size = size
    return _verify_semaphore


def _check_html_entry(path: Path, *, has_external_js: bool = False) -> list[str]:
    issues: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:8000]
    except OSError as exc:
        return [f"html_read_error:{exc}"]
    lower = text.lower()
    if "<!doctype" not in lower and "<html" not in lower:
        issues.append("html_missing_doctype_or_html_tag")
    if not has_external_js and "<script" not in lower and ".js" not in lower:
        issues.append("html_missing_script_reference")
    return issues


@dataclass
class ProjectVerifyResult:
    ok: bool
    backend: str
    status: str  # ok | failed | degraded | skipped
    stage: str = ""
    issues: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    written_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "backend": self.backend,
            "status": self.status,
            "stage": self.stage,
            "issues": self.issues,
            "stdout": self.stdout[:4000],
            "stderr": self.stderr[:4000],
            "duration_ms": self.duration_ms,
            "written_files": self.written_files,
        }


def _session_root(task_id: str) -> Path:
    return task_artifact_dir(task_id).resolve()


def _detect_language(goal: str, files: list[dict[str, str]]) -> str:
    text = goal.lower()
    for path in (f.get("path") or "" for f in files):
        lower = path.lower()
        if lower.endswith(".cpp") or lower.endswith(".cc"):
            return "cpp"
        if lower.endswith(".py"):
            return "python"
    if "python" in text or ".py" in text:
        return "python"
    if "c++" in text or "cpp" in text:
        return "cpp"
    return ""


def resolve_project_backend_id(
    *,
    intent_kind: str,
    goal: str = "",
    files: list[dict[str, str]] | None = None,
) -> str | None:
    cfg = load_project_verify_config()
    kind = (intent_kind or "").strip().lower()
    if kind in cfg.backend_by_intent:
        bid = cfg.backend_by_intent[kind]
        if bid in cfg.backends or bid in ("cpp", "python"):
            return bid
    lang = _detect_language(goal, files or [])
    if lang and lang in cfg.backend_by_language:
        return cfg.backend_by_language[lang]
    if lang:
        resolved = resolve_backend_id(lang)
        if resolved:
            return resolved
    return None


def _copy_tree(src: Path, dst: Path, *, max_files: int, max_bytes: int) -> list[str]:
    written: list[str] = []
    count = 0
    for item in sorted(src.rglob("*")):
        if item.is_dir():
            continue
        rel = item.relative_to(src)
        if count >= max_files:
            break
        if item.stat().st_size > max_bytes:
            raise ValueError(f"file too large: {rel}")
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        written.append(str(rel))
        count += 1
    return written


def _find_entry_js(root: Path, names: frozenset[str]) -> Path | None:
    for name in names:
        for hit in root.rglob(name):
            if hit.is_file():
                return hit
    return None


def _find_required_file(project_root: Path, name: str) -> Path | None:
    direct = project_root / name
    if direct.is_file():
        return direct
    for hit in project_root.rglob(name):
        if hit.is_file():
            return hit
    return None


def _verify_web_html_js(
    project_root: Path,
    backend: ProjectBackendConfig,
    *,
    timeout_sec: int,
) -> ProjectVerifyResult:
    issues: list[str] = []
    for req in backend.required_files:
        if _find_required_file(project_root, req) is None:
            issues.append(f"missing_required:{req}")
    entry = _find_entry_js(project_root, backend.entry_js_names)
    html_path = _find_required_file(project_root, "index.html")
    if html_path is not None:
        issues.extend(_check_html_entry(html_path, has_external_js=entry is not None))
    if entry is None:
        issues.append("missing_entry_js")
    if issues:
        return ProjectVerifyResult(
            ok=False,
            backend=backend.id,
            status="failed",
            stage="structure",
            issues=issues,
        )
    cmd_tpl = backend.commands.get("syntax_check")
    if not cmd_tpl:
        return ProjectVerifyResult(
            ok=True,
            backend=backend.id,
            status="degraded",
            stage="syntax_check",
            issues=["no_syntax_check_command"],
        )
    entry_str = str(entry.resolve())
    if ";" in entry_str or "|" in entry_str or entry_str.startswith("-"):
        return ProjectVerifyResult(
            ok=False,
            backend=backend.id,
            status="failed",
            stage="syntax_check",
            issues=["entry_path_rejected"],
        )
    cmd = expand_cmd(cmd_tpl, file_path=entry_str)
    if any(";" in part or "|" in part or "&" in part for part in cmd):
        return ProjectVerifyResult(
            ok=False,
            backend=backend.id,
            status="failed",
            stage="syntax_check",
            issues=["command_injection_rejected"],
        )
    code, stdout, stderr, duration_ms = run_command(cmd, timeout_sec=timeout_sec, cwd=str(project_root))
    ok = code == 0
    return ProjectVerifyResult(
        ok=ok,
        backend=backend.id,
        status="ok" if ok else "failed",
        stage="syntax_check",
        issues=[] if ok else [f"node_check exit={code}"],
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
    )


def _verify_make_cpp_demo(
    project_root: Path,
    backend: ProjectBackendConfig,
    *,
    timeout_sec: int,
) -> ProjectVerifyResult:
    issues: list[str] = []
    makefile = _find_required_file(project_root, "Makefile")
    if makefile is None:
        for req in backend.required_files:
            if _find_required_file(project_root, req) is None:
                issues.append(f"missing_required:{req}")
    if issues:
        return ProjectVerifyResult(
            ok=False,
            backend=backend.id,
            status="failed",
            stage="structure",
            issues=issues,
        )
    cmd_tpl = backend.commands.get("build")
    if not cmd_tpl:
        return ProjectVerifyResult(
            ok=False,
            backend=backend.id,
            status="failed",
            stage="build",
            issues=["no_build_command"],
        )
    if cmd_tpl != ("make", "demo") and list(cmd_tpl)[:2] != ["make", "demo"]:
        return ProjectVerifyResult(
            ok=False,
            backend=backend.id,
            status="failed",
            stage="build",
            issues=["command_not_whitelisted"],
        )
    build_cwd = str(makefile.parent) if makefile is not None else str(project_root)
    code, stdout, stderr, duration_ms = run_command(
        list(cmd_tpl),
        timeout_sec=timeout_sec,
        cwd=build_cwd,
    )
    ok = code == 0
    return ProjectVerifyResult(
        ok=ok,
        backend=backend.id,
        status="ok" if ok else "failed",
        stage="build",
        issues=[] if ok else [f"make_demo exit={code}"],
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
    )


def verify_project(
    task_id: str,
    *,
    intent_kind: str,
    goal: str = "",
    backend_id: str | None = None,
) -> ProjectVerifyResult:
    cfg = load_project_verify_config()
    if not cfg.enabled:
        return ProjectVerifyResult(
            ok=True,
            backend="",
            status="skipped",
            issues=["verify_disabled"],
        )

    session = _session_root(task_id)
    if not session.is_dir():
        return ProjectVerifyResult(
            ok=False,
            backend="",
            status="failed",
            issues=["session_root_missing"],
        )

    bid = backend_id or resolve_project_backend_id(intent_kind=intent_kind, goal=goal)
    if not bid:
        return ProjectVerifyResult(
            ok=False,
            backend="",
            status="failed",
            issues=["no_backend_for_intent"],
        )
    if bid not in allowed_backend_ids():
        return ProjectVerifyResult(
            ok=False,
            backend=bid,
            status="failed",
            issues=["backend_not_whitelisted"],
        )

    sem = _verify_slot()
    if not sem.acquire(blocking=False):
        return ProjectVerifyResult(
            ok=False,
            backend=bid,
            status="degraded",
            issues=["verify_concurrency_limit"],
        )
    try:
        return _verify_project_locked(
            task_id,
            cfg=cfg,
            bid=bid,
            intent_kind=intent_kind,
            goal=goal,
        )
    finally:
        sem.release()


def _verify_project_locked(
    task_id: str,
    *,
    cfg: Any,
    bid: str,
    intent_kind: str,
    goal: str,
) -> ProjectVerifyResult:
    session = _session_root(task_id)
    work = Path(cfg.workspace_root) / task_id / uuid.uuid4().hex[:12]
    work.mkdir(parents=True, exist_ok=True)
    try:
        written = _copy_tree(session, work, max_files=cfg.max_files, max_bytes=cfg.max_file_bytes)
    except ValueError as exc:
        return ProjectVerifyResult(
            ok=False,
            backend=bid,
            status="failed",
            issues=[str(exc)],
        )

    if bid in ("cpp", "python"):
        lang = bid
        source = None
        for ext in (".cpp", ".cc", ".py"):
            for hit in work.rglob(f"*{ext}"):
                if hit.is_file():
                    source = hit
                    break
            if source:
                break
        if source is None:
            return ProjectVerifyResult(
                ok=False,
                backend=bid,
                status="failed",
                issues=["no_source_file"],
                written_files=written,
            )
        vr: VerifyResult = verify_source(
            source.read_text(encoding="utf-8"),
            language=lang,
            backend_id=bid,
            task_id=task_id,
            filename=source.name,
        )
        status = "ok" if vr.ok else "failed"
        if vr.skipped_reason:
            status = "skipped"
        return ProjectVerifyResult(
            ok=vr.ok,
            backend=bid,
            status=status,
            stage=vr.stage,
            issues=list(vr.issues or []),
            stdout=vr.stdout,
            stderr=vr.stderr,
            duration_ms=vr.duration_ms,
            written_files=written,
        )

    backend = cfg.backends.get(bid)
    if backend is None or not backend.enabled:
        return ProjectVerifyResult(
            ok=False,
            backend=bid,
            status="failed",
            issues=["backend_disabled"],
            written_files=written,
        )

    if bid == "web_html_js":
        result = _verify_web_html_js(work, backend, timeout_sec=cfg.timeout_sec)
    elif bid == "make_cpp_demo":
        result = _verify_make_cpp_demo(work, backend, timeout_sec=cfg.timeout_sec)
    else:
        return ProjectVerifyResult(
            ok=False,
            backend=bid,
            status="failed",
            issues=["unknown_backend"],
            written_files=written,
        )
    result.written_files = written
    return result


def slug_from_goal(goal: str, *, prefix: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "-", (goal or "demo").strip().lower())
    text = re.sub(r"-+", "-", text).strip("-")[:32] or "demo"
    return f"{prefix}/{text}"
