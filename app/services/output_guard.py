"""
Output guardrails — segment-aware PII detection and optional LLM review (Ch18).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from app.config.settings import settings
from app.services.content_segments import prose_text, split_content_segments


# Boundaries prevent matching substrings inside long numeric literals.
PII_PATTERNS: list[tuple[str, str]] = [
    ("email", r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    ("phone_cn", r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)"),
    ("id_cn", r"(?<!\d)\d{17}[\dXx](?!\d)"),
    ("credit_card", r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
    ("ipv4", r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"),
]


OUTPUT_GUARD_ROLE = """You are the output safety reviewer. Given candidate answer text, return ONE JSON:
- "passed": boolean
- "issues": list of strings (PII leaks, harmful content, policy violations)
- "sanitized_summary": optional safer replacement summary when passed is false but fixable"""


@dataclass
class GuardResult:
    passed: bool
    issues: list[str]
    sanitized_summary: str = ""
    source: str = "rules"

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "issues": self.issues,
            "sanitized_summary": self.sanitized_summary,
            "source": self.source,
        }


def scan_pii(text: str, *, prose_only: bool = True) -> list[str]:
    """Scan for PII patterns; by default only natural-language prose segments."""
    if prose_only:
        segments = split_content_segments(text)
        scan_target = prose_text(segments) if segments else text
    else:
        scan_target = text
    issues: list[str] = []
    for label, pattern in PII_PATTERNS:
        if re.search(pattern, scan_target):
            issues.append(f"pii:{label}")
    return issues


def _llm_review(text: str) -> GuardResult:
    from app.config.prompts import agent_system_prompt
    from app.services.llm_client import invoke_structured

    system = agent_system_prompt(OUTPUT_GUARD_ROLE)
    try:
        result = invoke_structured(
            "routing",
            system,
            json.dumps({"candidate_answer": text[:8000]}, ensure_ascii=False),
        )
        passed = bool(result.get("passed", True))
        issues = [str(i) for i in (result.get("issues") or [])]
        sanitized = str(result.get("sanitized_summary") or "")
        return GuardResult(
            passed=passed,
            issues=issues,
            sanitized_summary=sanitized,
            source="llm",
        )
    except (ValueError, RuntimeError):
        return GuardResult(passed=True, issues=[], source="llm_fallback")


def evaluate_output(text: str, *, llm_review: Optional[bool] = None) -> GuardResult:
    """Run segment-aware PII scan on prose and optional LLM review on full text."""
    if not text.strip():
        return GuardResult(passed=True, issues=[], source="empty")

    prose_only = getattr(settings, "OUTPUT_GUARD_PII_PROSE_ONLY", True)
    issues = scan_pii(text, prose_only=prose_only)
    if issues:
        return GuardResult(
            passed=False,
            issues=issues,
            source="pii_rules",
        )

    use_llm = (
        llm_review
        if llm_review is not None
        else _coerce_bool_output_guard(getattr(settings, "OUTPUT_GUARD_LLM_REVIEW", False))
    )
    if use_llm and settings.MODEL_ENABLED:
        return _llm_review(text)

    return GuardResult(passed=True, issues=[], source="rules")


def _coerce_bool_output_guard(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("1", "true", "yes", "on")
    return bool(value)
