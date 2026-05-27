"""Verify structured code artifacts and optional compile-error repair."""

from __future__ import annotations

from typing import Any

from app.services.code_verify.backends import get_backend, verify_source
from app.services.code_verify.config import load_verify_config, resolve_backend_id
from app.services.code_verify.models import VerifyResult


def verify_code_artifacts(
    artifacts: list[dict[str, Any]],
    *,
    task_id: str = "verify",
) -> list[VerifyResult]:
    cfg = load_verify_config()
    if not cfg.enabled:
        return []

    results: list[VerifyResult] = []
    for item in artifacts:
        if str(item.get("kind") or "").lower() != "code":
            continue
        lang = str(item.get("language") or "cpp")
        content = str(item.get("content") or "")
        if not content.strip():
            results.append(
                VerifyResult(
                    ok=False,
                    backend="",
                    stage="skipped",
                    language=lang,
                    skipped_reason="empty_content",
                    issues=["empty_content"],
                )
            )
            continue
        bid = resolve_backend_id(lang)
        if bid and get_backend(bid) is None:
            results.append(
                VerifyResult(
                    ok=True,
                    backend=bid,
                    stage="skipped",
                    language=lang,
                    skipped_reason="backend_not_configured",
                )
            )
            continue
        results.append(
            verify_source(
                content,
                language=lang,
                backend_id=bid,
                task_id=task_id,
            )
        )
    return results


def verify_reports_all_ok(reports: list[VerifyResult]) -> bool:
    return all(r.ok for r in reports)


def compile_stderr_summary(reports: list[VerifyResult]) -> str:
    parts: list[str] = []
    for r in reports:
        if r.ok:
            continue
        if r.stderr:
            parts.append(r.stderr.strip()[:6000])
        elif r.issues:
            parts.append("; ".join(r.issues))
    return "\n\n".join(parts)[:8000]
