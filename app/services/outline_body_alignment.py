from __future__ import annotations

import json
from typing import Any, Optional

from app.domain.writing_memory_models import (
    AlignmentDecision,
    BodyAction,
    BridgeSpec,
    ChangeLevel,
    OutlineDiffResult,
    PatchInstruction,
)
from app.config.settings import settings
from app.services.llm_client import invoke_structured
from app.services.outline_diff import compute_outline_diff_heuristic

# Re-export for backward compatibility
__all__ = [
    "AlignmentDecision",
    "BodyAction",
    "ChangeLevel",
    "decide_outline_body_alignment",
]


_SYSTEM = """You are a writing-orchestration router.

Goal: decide how to reconcile an existing manuscript body after an outline rewrite.

Rules:
- Output ONE JSON object with keys:
  change_level (minor|moderate|major),
  body_action (keep_append|append_with_bridge|patch_recent_chapters|rewrite_body),
  reason (short string),
  affected_chapters (list of int),
  continuity_risks (list of strings),
  confidence (0-1 float).
- Prefer keep_append for wording-only tweaks.
- Use append_with_bridge when plot direction shifts slightly but prior chapters mostly stand.
- Use patch_recent_chapters when only the last 1-3 chapters need edits.
- Use rewrite_body only when protagonist, POV, world rules, or timeline are invalidated.
"""


def _decision_from_diff(
    diff: OutlineDiffResult,
    *,
    last_chapter_index: int,
    body_total_chars: int,
) -> AlignmentDecision:
    recent_window = max(1, int(getattr(settings, "WRITING_ALIGNMENT_RECENT_WINDOW", 2)))
    max_patch_chapters = max(
        1, int(getattr(settings, "WRITING_ALIGNMENT_PATCH_MAX_CHAPTERS", 3))
    )
    bridge_default_chars = max(
        200, int(getattr(settings, "WRITING_BRIDGE_DEFAULT_CHARS", 600))
    )
    severity = diff.severity
    affected = diff.affected_chapters or []
    if not affected and last_chapter_index > 0:
        affected = list(range(max(1, last_chapter_index - 2), last_chapter_index + 1))

    if severity in ("trivial", "minor"):
        return AlignmentDecision(
            change_level="minor",
            body_action="keep_append",
            reason=diff.summary or "minor outline tweak",
            affected_chapters=affected,
            confidence=0.85,
        )

    if severity == "major" or len(diff.removed_plot_points) >= 2:
        return AlignmentDecision(
            change_level="major",
            body_action="rewrite_body",
            reason=diff.summary or "major outline restructuring",
            affected_chapters=affected,
            continuity_risks=["主线或世界观变更"],
            confidence=0.9,
        )

    recent = [c for c in affected if c >= max(1, last_chapter_index - recent_window)]
    if len(recent) >= 2 or len(diff.modified_plot_points) >= 2:
        patches = [
            PatchInstruction(
                chapter_index=ch,
                patch_type="rewrite_ending",
                target_section="章末衔接段",
                instruction="按新大纲调整章末情节与伏笔，保持前文主体不变",
            )
            for ch in sorted(set(recent))[:max_patch_chapters]
        ]
        return AlignmentDecision(
            change_level="moderate",
            body_action="patch_recent_chapters",
            reason=diff.summary or "recent chapters need localized patch",
            affected_chapters=sorted(set(recent)),
            patch_instructions=patches,
            continuity_risks=["近章情节与新大纲不一致"],
            confidence=0.78,
        )

    bridge_after = max(0, last_chapter_index)
    return AlignmentDecision(
        change_level="moderate",
        body_action="append_with_bridge",
        reason=diff.summary or "moderate outline shift — bridge before next chapter",
        affected_chapters=affected,
        bridge_spec=BridgeSpec(
            insert_after_chapter=bridge_after,
            target_chars=bridge_default_chars,
            bridge_goal="衔接旧正文与新大纲，化解情节断层",
            must_resolve=diff.summary.split()[:5] if diff.summary else [],
        ),
        continuity_risks=["续写可能出现情节跳跃"],
        confidence=0.75,
    )


def decide_outline_body_alignment(
    *,
    outline_before_excerpt: str,
    outline_after_excerpt: str,
    body_tail_excerpt: str,
    body_total_chars: int,
    last_chapter_index: int,
    user_goal: str = "",
    extra: Optional[dict[str, Any]] = None,
    outline_diff: Optional[OutlineDiffResult] = None,
) -> AlignmentDecision:
    diff = outline_diff or compute_outline_diff_heuristic(
        outline_before_excerpt,
        outline_after_excerpt,
    )
    rule_decision = _decision_from_diff(
        diff,
        last_chapter_index=last_chapter_index,
        body_total_chars=body_total_chars,
    )

    payload: dict[str, Any] = {
        "user_goal": (user_goal or "")[:400],
        "outline_before_excerpt": (outline_before_excerpt or "")[:6000],
        "outline_after_excerpt": (outline_after_excerpt or "")[:6000],
        "body_tail_excerpt": (body_tail_excerpt or "")[:3000],
        "body_total_chars": int(body_total_chars or 0),
        "last_chapter_index": int(last_chapter_index or 0),
        "outline_diff": diff.to_dict(),
        "rule_decision": rule_decision.to_dict(),
        "extra": extra or {},
    }
    try:
        result = invoke_structured("routing", _SYSTEM, json.dumps(payload, ensure_ascii=False))
        return _merge_llm_decision(rule_decision, result)
    except Exception:
        return rule_decision


def _merge_llm_decision(
    rule: AlignmentDecision,
    result: dict[str, Any],
) -> AlignmentDecision:
    level = str(result.get("change_level") or rule.change_level)
    if level not in ("minor", "moderate", "major"):
        level = rule.change_level
    action = str(result.get("body_action") or rule.body_action)
    allowed: tuple[BodyAction, ...] = (
        "keep_append",
        "append_with_bridge",
        "patch_recent_chapters",
        "rewrite_body",
    )
    if action not in allowed:
        action = rule.body_action
    reason = str(result.get("reason") or rule.reason).strip() or rule.reason
    affected = result.get("affected_chapters")
    if not isinstance(affected, list):
        affected = rule.affected_chapters
    else:
        affected = [int(x) for x in affected if str(x).isdigit()]
    risks = result.get("continuity_risks")
    if not isinstance(risks, list):
        risks = rule.continuity_risks
    confidence = float(result.get("confidence") or rule.confidence)
    decision = AlignmentDecision(
        change_level=level,  # type: ignore[assignment]
        body_action=action,  # type: ignore[assignment]
        reason=reason,
        affected_chapters=affected,
        bridge_spec=rule.bridge_spec,
        patch_instructions=list(rule.patch_instructions),
        continuity_risks=[str(r) for r in risks],
        confidence=min(1.0, max(0.0, confidence)),
    )
    if action == "append_with_bridge" and not decision.bridge_spec:
        decision.bridge_spec = BridgeSpec(
            insert_after_chapter=max(0, int(result.get("insert_after_chapter") or 0)),
            target_chars=max(
                200, int(getattr(settings, "WRITING_BRIDGE_DEFAULT_CHARS", 600))
            ),
            bridge_goal=reason,
            must_resolve=decision.continuity_risks,
        )
    return decision
