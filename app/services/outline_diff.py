"""Structured outline diff for replan

alignment pipeline."""

from __future__ import annotations

import json
import re
from typing import Optional

from app.domain.writing_memory_models import OutlineDiffResult, PlotPoint
from app.services.manuscript_context import cn_numeral_to_int

_CHAPTER_LINE = re.compile(
    r"(?:^|\n)(?:#{1,3}\s*)?第\s*([一二三四五六七八九十百零两\d]+)\s*章[^\n]*",
    re.MULTILINE,
)


def _chapter_sections(outline: str) -> dict[int, str]:
    sections: dict[int, list[str]] = {}
    current: Optional[int] = None
    for line in (outline or "").splitlines():
        m = _CHAPTER_LINE.search(line)
        if m:
            num = cn_numeral_to_int(m.group(1))
            if num is not None:
                current = num
                sections.setdefault(current, [line])
                continue
        if current is not None:
            sections.setdefault(current, []).append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def compute_outline_diff_heuristic(
    old_outline: str,
    new_outline: str,
) -> OutlineDiffResult:
    old_secs = _chapter_sections(old_outline)
    new_secs = _chapter_sections(new_outline)
    all_chapters = sorted(set(old_secs) | set(new_secs))

    added: list[PlotPoint] = []
    removed: list[PlotPoint] = []
    modified: list[tuple[PlotPoint, PlotPoint]] = []
    affected: list[int] = []

    for ch in all_chapters:
        old_t = old_secs.get(ch, "")
        new_t = new_secs.get(ch, "")
        if not old_t and new_t:
            added.append(PlotPoint(ch, f"第{ch}章", new_t[:200]))
            affected.append(ch)
        elif old_t and not new_t:
            removed.append(PlotPoint(ch, f"第{ch}章", old_t[:200]))
            affected.append(ch)
        elif old_t and new_t:
            ratio = _similarity(old_t, new_t)
            if ratio < 0.72:
                modified.append(
                    (
                        PlotPoint(ch, f"第{ch}章", old_t[:200]),
                        PlotPoint(ch, f"第{ch}章", new_t[:200]),
                    )
                )
                affected.append(ch)

    severity = _severity_from_diff(len(added), len(removed), len(modified), affected)
    summary = (
        f"outline diff: +{len(added)} -{len(removed)} ~{len(modified)} "
        f"severity={severity} chapters={affected[:8]}"
    )
    return OutlineDiffResult(
        added_plot_points=added,
        removed_plot_points=removed,
        modified_plot_points=modified,
        affected_chapters=sorted(set(affected)),
        severity=severity,
        summary=summary,
    )


def _similarity(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _severity_from_diff(
    n_added: int,
    n_removed: int,
    n_modified: int,
    affected: list[int],
) -> str:
    total = n_added + n_removed + n_modified
    if total == 0:
        return "trivial"
    if n_removed >= 2 or (n_modified >= 3 and len(affected) >= 4):
        return "major"
    if n_modified >= 2 or n_removed >= 1 or len(affected) >= 3:
        return "moderate"
    if total == 1 and n_modified <= 1:
        return "minor"
    return "moderate"


def compute_outline_diff(
    old_outline: str,
    new_outline: str,
    *,
    use_llm: bool = True,
) -> OutlineDiffResult:
    heuristic = compute_outline_diff_heuristic(old_outline, new_outline)
    if not use_llm or heuristic.severity == "trivial":
        return heuristic
    try:
        from app.services.llm_client import invoke_structured

        system = (
            "Return JSON: added_plot_points, removed_plot_points, modified_plot_points "
            "(list of {old, new}), affected_chapters (list of int), "
            "severity (trivial|minor|moderate|major), summary (string)."
        )
        payload = {
            "outline_before": (old_outline or "")[:8000],
            "outline_after": (new_outline or "")[:8000],
            "heuristic_hint": heuristic.to_dict(),
        }
        result = invoke_structured(
            "routing",
            system,
            json.dumps(payload, ensure_ascii=False),
        )
        return _merge_llm_diff(heuristic, result)
    except Exception:
        return heuristic


def _merge_llm_diff(
    heuristic: OutlineDiffResult,
    result: dict,
) -> OutlineDiffResult:
    sev = str(result.get("severity") or heuristic.severity)
    if sev not in ("trivial", "minor", "moderate", "major"):
        sev = heuristic.severity
    affected = result.get("affected_chapters")
    if not isinstance(affected, list) or not affected:
        affected = heuristic.affected_chapters
    else:
        affected = [int(x) for x in affected if str(x).isdigit()]
    return OutlineDiffResult(
        added_plot_points=heuristic.added_plot_points,
        removed_plot_points=heuristic.removed_plot_points,
        modified_plot_points=heuristic.modified_plot_points,
        affected_chapters=affected,
        severity=sev,  # type: ignore[assignment]
        summary=str(result.get("summary") or heuristic.summary),
    )
