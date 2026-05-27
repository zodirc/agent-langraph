"""
Code artifact pipeline: compile verification + deterministic / LLM repair with graded fallback.

Deterministic repairs run first for common token corruption; compiler stderr then drives
LLM repair. Low-risk code tasks degrade gracefully instead of forcing human review.
Configurable via config.yaml → code_artifact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.config.settings import settings
from app.services.metrics_service import get_metrics_service


@dataclass(frozen=True)
class CodeArtifactConfig:
    enabled: bool = True
    repair_enabled: bool = True
    stream_mode: str = "composed_at_end"  # composed_at_end | artifact_incremental


def load_code_artifact_config() -> CodeArtifactConfig:
    raw = getattr(settings, "CODE_ARTIFACT_CONFIG", None)
    if not isinstance(raw, dict):
        return CodeArtifactConfig()
    repair = raw.get("repair") or {}
    stream = raw.get("stream") or {}
    return CodeArtifactConfig(
        enabled=bool(raw.get("enabled", True)),
        repair_enabled=bool(repair.get("enabled", True)),
        stream_mode=str(stream.get("mode", "composed_at_end")),
    )


def iter_code_artifacts(structured: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(structured, dict):
        return []
    out: list[dict[str, Any]] = []
    for item in structured.get("artifacts") or []:
        if isinstance(item, dict) and str(item.get("kind") or "").lower() == "code":
            out.append(item)
    return out


def should_process_code_artifacts(state: dict[str, Any] | None = None) -> bool:
    cfg = load_code_artifact_config()
    if not cfg.enabled:
        return False
    if state:
        payload = state.get("input_payload") or {}
        audit = payload.get("route_audit") or {}
        profile = str(
            payload.get("artifact_profile") or audit.get("artifact_profile") or ""
        ).lower()
        if profile == "source_code":
            return True
        if str(audit.get("inferred_kind") or "") == "code":
            return True
    return True


def stream_artifact_incremental_enabled() -> bool:
    cfg = load_code_artifact_config()
    if not cfg.enabled:
        return False
    return cfg.stream_mode == "artifact_incremental"


def _repair_purpose() -> str:
    raw = getattr(settings, "CODE_ARTIFACT_CONFIG", None)
    if isinstance(raw, dict):
        repair = raw.get("repair") or {}
        if isinstance(repair, dict) and repair.get("llm_purpose"):
            return str(repair["llm_purpose"])
    return "reasoning"


def repair_artifacts_via_llm(
    artifacts: list[dict[str, Any]],
    *,
    goal: str,
    language: str,
    compile_errors: str,
    trace_state: Any | None = None,
    budget_ctx: Any | None = None,
) -> list[dict[str, Any]] | None:
    """LLM repair using compiler stderr (only path — no heuristic format repair)."""
    if not compile_errors.strip():
        return None

    from app.services.llm_client import _extract_json, _normalize_content, get_llm
    from app.services.langsmith_setup import runnable_config_with_trace

    purpose = _repair_purpose()
    llm = get_llm(purpose, budget_ctx=budget_ctx)
    if llm is None:
        return None

    from langchain_core.messages import HumanMessage, SystemMessage

    lang = language or "cpp"
    repair_system = (
        "Fix the source code so it compiles. Return ONE JSON object only:\n"
        '{"artifacts":[{"kind":"code","language":"'
        + lang
        + '","content":"..."}]}\n'
        "Rules:\n"
        "- Fix every error reported in compiler_stderr; preserve intended program logic.\n"
        "- Put the full source in artifacts[].content with real newlines (\\n); no markdown fences.\n"
        "- CRITICAL: Preserve all whitespace — tokens MUST be separated by spaces "
        "(e.g. '#ifndef SINGLETON_H' NOT '#ifndefSINGLETON_H', "
        "'class Foo {' NOT 'classFoo{').\n"
        "- Do not impose style rules beyond what compiler_stderr requires."
    )
    repair_user = json.dumps(
        {
            "goal": goal[:2000],
            "language": lang,
            "compiler_stderr": compile_errors[:8000],
            "artifacts_to_repair": [
                {
                    "language": a.get("language") or lang,
                    "content": str(a.get("content") or "")[:24000],
                }
                for a in artifacts
            ],
        },
        ensure_ascii=False,
    )
    run_config = runnable_config_with_trace(
        trace_state if isinstance(trace_state, dict) else None
    )
    try:
        response = llm.invoke(
            [SystemMessage(content=repair_system), HumanMessage(content=repair_user)],
            config=run_config or None,
        )
        content = response.content if hasattr(response, "content") else str(response)
        parsed = _extract_json(_normalize_content(str(content)), prefer_keys=("artifacts",))
        repaired = parsed.get("artifacts")
        if not isinstance(repaired, list):
            structured = parsed.get("structured")
            if isinstance(structured, dict):
                repaired = structured.get("artifacts")
        if not isinstance(repaired, list) or not repaired:
            return None
        out: list[dict[str, Any]] = []
        for item in repaired:
            if isinstance(item, dict) and str(item.get("content") or "").strip():
                out.append(
                    {
                        "kind": "code",
                        "language": str(item.get("language") or lang),
                        "content": str(item.get("content") or ""),
                    }
                )
        return out or None
    except Exception:
        return None


def _sanitize_collapsed_whitespace(content: str, language: str) -> str:
    """
    Detect and repair whitespace-collapsed tokens in source code.

    Streaming chunk assembly can collapse ``"#ifndef "`` + ``"SINGLETON_H"``
    into ``"#ifndefSINGLETON_H"`` when intermediate whitespace is stripped.
    This function re-inserts the missing space for known patterns.
    """
    import re as _re

    # C/C++ preprocessor directives that MUST be followed by a space
    # e.g. #ifndefSINGLETON_H → #ifndef SINGLETON_H
    _CPP_DIRECTIVE_RE = _re.compile(
        r"#(ifndef|ifdef|define|include|pragma|if|elif|undef|endif)\s*(\S)",
    )

    # Keyword-identifier collapses: detect keyword immediately followed by an
    # alphanumeric/underscore WITHOUT any space.  We cannot use \b because when
    # the keyword and identifier are smashed together (e.g. "classSingleton")
    # they form a single word token.  Instead we match common keyword spellings
    # at the START of a word boundary and look for a transition from the keyword
    # to a follow-on upper-case letter, digit or underscore that starts a new
    # identifier.
    _KW_LIST = (
        "class|struct|enum|namespace|return|typedef|using|template|typename|"
        "virtual|static|const|extern|inline|volatile|unsigned|signed|"
        "void|int|char|float|double|bool|long|short|auto|register|"
        "if|else|while|for|switch|case|default|break|continue|"
        "throw|catch|try|new|delete|public|private|protected|"
        "import|from|def|async|await|function|var|let|export"
    )
    # Match e.g. "classSingleton" → insert space before "Singleton"
    # Only trigger when followed by uppercase letter or underscore (avoids
    # false positives like "define" inside "predefined").
    _KEYWORD_COLLAPSE_RE = _re.compile(
        r"(?<![a-zA-Z_])(" + _KW_LIST + r")([A-Z_][a-zA-Z0-9_]*)",
    )

    fixed = content
    fixed = _CPP_DIRECTIVE_RE.sub(r"#\1 \2", fixed)
    fixed = _KEYWORD_COLLAPSE_RE.sub(r"\1 \2", fixed)
    return fixed


_DETERMINISTIC_SOURCE_FIXUPS: tuple[tuple[str, str], ...] = (
    ("static _cast", "static_cast"),
    ("dynamic _cast", "dynamic_cast"),
    ("const _cast", "const_cast"),
    ("reinterpret _cast", "reinterpret_cast"),
    ("std ::", "std::"),
    ("-> ", "->"),
)


def _apply_deterministic_code_fixes(content: str, language: str) -> str:
    fixed = _sanitize_collapsed_whitespace(content, language)
    for bad, good in _DETERMINISTIC_SOURCE_FIXUPS:
        fixed = fixed.replace(bad, good)
    return fixed


def _replace_code_artifacts(
    structured: dict[str, Any],
    repaired: list[dict[str, Any]],
) -> dict[str, Any]:
    non_code = [
        a
        for a in (structured.get("artifacts") or [])
        if not (isinstance(a, dict) and str(a.get("kind") or "").lower() == "code")
    ]
    structured["artifacts"] = non_code + repaired
    return structured


def _allow_failed_code_fallback(state: dict[str, Any] | None, structured: dict[str, Any]) -> bool:
    payload = (state or {}).get("input_payload") or {}
    audit = payload.get("route_audit") or {}
    inferred_kind = str(audit.get("inferred_kind") or "").lower()
    profile = str(payload.get("artifact_profile") or audit.get("artifact_profile") or "").lower()
    risk = str((state or {}).get("risk_level") or payload.get("risk_level") or "LOW").upper()
    return (
        risk == "LOW"
        and structured.get("code_verify_ok") is not True
        and inferred_kind in {"", "code", "qa"}
        and profile in {"", "source_code"}
    )


def ensure_code_artifacts_quality(
    reasoning_result: dict[str, Any],
    state: dict[str, Any] | None = None,
    *,
    goal: str = "",
    budget_ctx: Any | None = None,
) -> dict[str, Any]:
    """
    Run compile verify; on failure invoke LLM repair with stderr until pass or max attempts.

    Before verification, applies whitespace-collapse sanitization to each code
    artifact to repair token-stripping damage from streaming assembly.
    """
    cfg = load_code_artifact_config()
    if not cfg.enabled or not should_process_code_artifacts(state):
        return reasoning_result

    structured = dict(reasoning_result.get("structured") or {})
    code_items = iter_code_artifacts(structured)
    if not code_items:
        return reasoning_result

    # --- deterministic sanitization pass ---
    deterministic_repairs_applied = False
    for item in code_items:
        raw = str(item.get("content") or "")
        lang = str(item.get("language") or "")
        sanitized = _apply_deterministic_code_fixes(raw, lang)
        if sanitized != raw:
            item["content"] = sanitized
            deterministic_repairs_applied = True

    if deterministic_repairs_applied:
        structured["code_artifact_deterministic_repaired"] = True
        get_metrics_service().inc_contract_event("code_deterministic_repair")

    structured = _run_compile_verify_loop(
        structured,
        code_items=code_items,
        goal=goal or str((state or {}).get("input_payload", {}).get("goal") or ""),
        state=state,
        budget_ctx=budget_ctx,
        cfg=cfg,
    )
    return {**reasoning_result, "structured": structured}


def _run_compile_verify_loop(
    structured: dict[str, Any],
    *,
    code_items: list[dict[str, Any]],
    goal: str,
    state: dict[str, Any] | None,
    budget_ctx: Any | None,
    cfg: CodeArtifactConfig,
) -> dict[str, Any]:
    from app.services.code_verify.config import load_verify_config
    from app.services.code_verify.pipeline import (
        compile_stderr_summary,
        verify_code_artifacts,
        verify_reports_all_ok,
    )

    vcfg = load_verify_config()
    if not vcfg.enabled or not code_items:
        return structured

    task_id = str((state or {}).get("task_id") or "verify")
    lang = str(code_items[0].get("language") or "cpp")
    items = list(code_items)
    all_reports: list[dict[str, Any]] = []

    for attempt in range(max(1, vcfg.max_attempts)):
        reports = verify_code_artifacts(items, task_id=task_id)
        all_reports = [r.to_dict() for r in reports]
        if verify_reports_all_ok(reports):
            structured["code_verify_ok"] = True
            structured["code_verify_reports"] = all_reports
            structured.pop("code_verify_failed", None)
            structured.pop("code_verify_degraded", None)
            get_metrics_service().inc_contract_event("code_verify_ok")
            return structured

        get_metrics_service().inc_contract_event("code_verify_failed")
        if vcfg.on_failure != "repair" or not cfg.repair_enabled:
            break

        stderr = compile_stderr_summary(reports)
        repaired = repair_artifacts_via_llm(
            items,
            goal=goal,
            language=lang,
            compile_errors=stderr,
            trace_state=state,
            budget_ctx=budget_ctx,
        )
        if not repaired:
            structured["code_artifact_repair_failed"] = True
            get_metrics_service().inc_contract_event("code_repair_failed")
            break
        structured = _replace_code_artifacts(structured, repaired)
        items = repaired
        structured["code_artifact_repaired"] = True
        get_metrics_service().inc_contract_event("code_repair_llm")

    structured["code_verify_ok"] = False
    structured["code_verify_reports"] = all_reports
    structured["code_verify_failed"] = True
    if _allow_failed_code_fallback(state, structured):
        structured["code_verify_degraded"] = True
        structured["code_verify_degraded_reason"] = "low_risk_code_answer"
        get_metrics_service().inc_contract_event("code_verify_degraded")
    return structured


def filter_memory_hits(
    state: dict[str, Any] | None,
    hits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Suppress noisy session memories for short low-risk code QA turns."""
    from app.services.memory_query import should_suppress_session_memory

    if should_suppress_session_memory(state or {}):
        get_metrics_service().inc_contract_event("memory_hits_suppressed")
        return []
    return hits
