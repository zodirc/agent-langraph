"""Load route_audit rules from settings (config.yaml)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.config.settings import settings


@dataclass(frozen=True)
class KindRule:
    id: str
    weight: float = 1.0
    patterns: tuple[re.Pattern[str], ...] = ()
    structural: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ConflictRule:
    when_kind: str
    planned_route: str
    action: str
    unless_kind: str | None = None
    min_kind_score: float | None = None


@dataclass(frozen=True)
class RouteAuditConfig:
    enabled: bool = True
    min_kind_score: float = 0.35
    code_extensions: frozenset[str] = frozenset(
        {".cpp", ".cc", ".cxx", ".hpp", ".h", ".c", ".py", ".rs", ".go", ".java", ".js", ".ts"}
    )
    manuscript_body_names: frozenset[str] = frozenset({"novel.txt", "body.txt"})
    kinds: tuple[KindRule, ...] = ()
    conflicts: tuple[ConflictRule, ...] = ()
    reflection_on_misroute: bool = True
    max_planning_revisions: int = 1


def _compile_patterns(raw: Any) -> tuple[re.Pattern[str], ...]:
    if not isinstance(raw, list):
        return ()
    out: list[re.Pattern[str]] = []
    for item in raw:
        text = str(item).strip()
        if not text:
            continue
        try:
            out.append(re.compile(text, re.IGNORECASE | re.MULTILINE))
        except re.error:
            continue
    return tuple(out)


def load_route_audit_config() -> RouteAuditConfig:
    raw = getattr(settings, "ROUTE_AUDIT_CONFIG", None)
    if not isinstance(raw, dict):
        return RouteAuditConfig()

    kinds_raw = raw.get("kinds") or {}
    kinds: list[KindRule] = []
    if isinstance(kinds_raw, dict):
        for kind_id, spec in kinds_raw.items():
            if not isinstance(spec, dict):
                continue
            kinds.append(
                KindRule(
                    id=str(kind_id),
                    weight=float(spec.get("weight", 1.0)),
                    patterns=_compile_patterns(spec.get("patterns")),
                    structural=frozenset(str(s) for s in (spec.get("structural") or [])),
                )
            )

    conflicts_raw = raw.get("conflicts") or []
    conflicts: list[ConflictRule] = []
    if isinstance(conflicts_raw, list):
        for item in conflicts_raw:
            if not isinstance(item, dict):
                continue
            when_kind = str(item.get("when_kind") or "").strip()
            planned = str(item.get("planned_route") or "").strip()
            action = str(item.get("action") or "").strip()
            if not when_kind or not planned or not action:
                continue
            unless = item.get("unless_kind")
            conflicts.append(
                ConflictRule(
                    when_kind=when_kind,
                    planned_route=planned,
                    action=action,
                    unless_kind=str(unless).strip() if unless else None,
                    min_kind_score=(
                        float(item["min_kind_score"])
                        if item.get("min_kind_score") is not None
                        else None
                    ),
                )
            )

    ext_raw = raw.get("code_extensions")
    code_ext = (
        frozenset(str(e).lower() for e in ext_raw)
        if isinstance(ext_raw, list)
        else RouteAuditConfig().code_extensions
    )
    body_raw = raw.get("manuscript_body_names")
    body_names = (
        frozenset(str(n).lower() for n in body_raw)
        if isinstance(body_raw, list)
        else RouteAuditConfig().manuscript_body_names
    )

    return RouteAuditConfig(
        enabled=bool(raw.get("enabled", True)),
        min_kind_score=float(raw.get("min_kind_score", 0.35)),
        code_extensions=code_ext,
        manuscript_body_names=body_names,
        kinds=tuple(kinds),
        conflicts=tuple(conflicts),
        reflection_on_misroute=bool(raw.get("reflection_on_misroute", True)),
        max_planning_revisions=int(raw.get("max_planning_revisions", 1)),
    )
