"""Patch vs rewrite scope decision (optimization.md §3.7)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_GLOBAL_SCOPE_PATTERNS = (
    r"全部",
    r"所有",
    r"整套",
    r"整体",
    r"全局",
    r"整个人物",
    r"所有人物",
    r"整套人物",
    r"人物设定",
    r"架空",
    r"原电影",
    r"原作",
    r"换成.{0,6}名",
    r"rewrite\s+all",
    r"all\s+characters",
    r"entire\s+cast",
    r"whole\s+outline",
)

_GLOBAL_RE = re.compile("|".join(_GLOBAL_SCOPE_PATTERNS), re.IGNORECASE)
_RENAME_ARROW = re.compile(
    r"([\u4e00-\u9fffA-Za-z]{1,12})\s*(?:→|->|改为|改成|换成)\s*([\u4e00-\u9fffA-Za-z]{1,12})"
)
_CHARACTER_MENTION = re.compile(
    r"(?:人物|角色|主角|配角|姓名|名字|叫|名为)",
    re.IGNORECASE,
)
_BULK_REPLACE = re.compile(
    r"(?:都|全部|所有|整套|整体|全局).{0,8}(?:改|换|替换|重写)",
    re.IGNORECASE,
)


@dataclass
class EditScopeAnalysis:
    anchor_count: int = 0
    estimated_span_ratio: float = 0.0
    global_keywords: bool = False
    bulk_replace_hint: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor_count": self.anchor_count,
            "estimated_span_ratio": round(self.estimated_span_ratio, 4),
            "global_keywords": self.global_keywords,
            "bulk_replace_hint": self.bulk_replace_hint,
        }


def steer_implies_global_rewrite(steer_text: str) -> bool:
    text = (steer_text or "").strip()
    if not text:
        return False
    return bool(_GLOBAL_RE.search(text))


def analyze_edit_scope(
    steer_text: str,
    *,
    outline_excerpt: str = "",
) -> EditScopeAnalysis:
    """Estimate anchor count and edit span from steer phrasing + outline excerpt."""
    steer = (steer_text or "").strip()
    excerpt = (outline_excerpt or "").strip()
    global_kw = steer_implies_global_rewrite(steer)
    bulk = bool(_BULK_REPLACE.search(steer))

    rename_pairs = _RENAME_ARROW.findall(steer)
    anchor_count = len(rename_pairs)
    if anchor_count == 0 and _CHARACTER_MENTION.search(steer):
        anchor_count = max(1, steer.count("、") + steer.count(",") + 1)

    span_chars = 0
    if excerpt and rename_pairs:
        for old, _new in rename_pairs:
            span_chars += excerpt.count(old) * max(len(old), 1)
    elif excerpt and global_kw:
        span_chars = int(len(excerpt) * 0.35)
    elif excerpt and bulk:
        span_chars = int(len(excerpt) * 0.2)

    ratio = (span_chars / len(excerpt)) if excerpt and span_chars else 0.0
    if global_kw and excerpt:
        ratio = max(ratio, 0.25)

    return EditScopeAnalysis(
        anchor_count=anchor_count,
        estimated_span_ratio=min(1.0, ratio),
        global_keywords=global_kw,
        bulk_replace_hint=bulk,
    )


def should_rewrite_outline_not_patch(
    steer_text: str,
    *,
    outline_excerpt: str = "",
    anchor_count: int = 0,
    estimated_span_ratio: float = 0.0,
    scope: EditScopeAnalysis | None = None,
) -> bool:
    """
    True when user intent implies global character/outline rewrite rather than minimal patch.
    """
    analysis = scope or analyze_edit_scope(steer_text, outline_excerpt=outline_excerpt)
    if analysis.global_keywords or analysis.bulk_replace_hint:
        return True
    if anchor_count <= 0:
        anchor_count = analysis.anchor_count
    if estimated_span_ratio <= 0:
        estimated_span_ratio = analysis.estimated_span_ratio
    if anchor_count >= 4:
        return True
    if estimated_span_ratio >= 0.25:
        return True
    return False


def classify_edit_action(
    steer_text: str,
    *,
    outline_exists: bool,
    outline_excerpt: str = "",
) -> str:
    """Return rewrite_outline or edit_plot."""
    if not outline_exists:
        return "rewrite_outline"
    scope = analyze_edit_scope(steer_text, outline_excerpt=outline_excerpt)
    if should_rewrite_outline_not_patch(steer_text, outline_excerpt=outline_excerpt, scope=scope):
        return "rewrite_outline"
    return "edit_plot"
