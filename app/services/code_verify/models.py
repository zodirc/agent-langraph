"""Unified verification result (language-agnostic)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class VerifyResult:
    ok: bool
    backend: str
    stage: str  # compile | link | run | skipped
    language: str
    exit_code: int | None = None
    duration_ms: int = 0
    stdout: str = ""
    stderr: str = ""
    source_path: str = ""
    issues: list[str] = field(default_factory=list)
    skipped_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "backend": self.backend,
            "stage": self.stage,
            "language": self.language,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "stdout": (self.stdout or "")[:4000],
            "stderr": (self.stderr or "")[:4000],
            "source_path": self.source_path,
            "issues": self.issues,
            "skipped_reason": self.skipped_reason,
        }
