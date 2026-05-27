from __future__ import annotations

from typing import Any, Optional

from app.config.settings import settings
from app.domain.decision import PolicyDecision


class PolicyEngine:
    def evaluate(
        self,
        *,
        reasoning_result: Optional[dict[str, Any]] = None,
        tool_results: Optional[list[dict[str, Any]]] = None,
        risk_level: Optional[str] = None,
        user_role: str = "user",
        review_required: bool = False,
    ) -> PolicyDecision:
        inferred_risk = (risk_level or _infer_risk(reasoning_result, tool_results)).upper()

        if review_required:
            return PolicyDecision(
                result="REVIEW",
                reason="Explicit review flag set",
                risk_level=inferred_risk,
            )

        if inferred_risk == settings.AUTO_REJECT_RISK_LEVEL:
            return PolicyDecision(
                result="REJECT",
                reason=f"Risk level {inferred_risk} triggers auto reject",
                risk_level=inferred_risk,
            )

        if inferred_risk == "UNKNOWN":
            return PolicyDecision(
                result="ESCALATE",
                reason="Unknown risk requires escalation",
                risk_level=inferred_risk,
            )

        if inferred_risk == settings.REQUIRE_REVIEW_RISK_LEVEL:
            return PolicyDecision(
                result="REVIEW",
                reason=f"Risk level {inferred_risk} requires human review",
                risk_level=inferred_risk,
            )

        if reasoning_result and _is_parser_format_recovery(reasoning_result):
            return PolicyDecision(
                result="CONTINUE",
                reason="Parser-format recovery (non-epistemic confidence)",
                risk_level=inferred_risk,
                metadata={"parser_format_recovery": True},
            )

        structured = (
            reasoning_result.get("structured")
            if isinstance(reasoning_result.get("structured"), dict)
            else {}
        )
        if structured.get("code_verify_failed") and structured.get("code_verify_ok") is not True:
            if structured.get("code_verify_degraded"):
                return PolicyDecision(
                    result="CONTINUE",
                    reason="Source code failed verification but low-risk degraded delivery is allowed",
                    risk_level=inferred_risk,
                    metadata={"code_verify_failed": True, "code_verify_degraded": True},
                )
            return PolicyDecision(
                result="REVIEW",
                reason="Source code failed compile verification",
                risk_level=inferred_risk,
                metadata={"code_verify_failed": True},
            )

        confidence = 1.0
        if reasoning_result:
            confidence = float(reasoning_result.get("confidence", 0.8))
        if confidence < 0.5:
            return PolicyDecision(
                result="REVIEW",
                reason=f"Low confidence ({confidence})",
                risk_level=inferred_risk,
                metadata={"confidence": confidence},
            )

        if user_role == "guest" and _has_high_risk_tools(tool_results):
            return PolicyDecision(
                result="REJECT",
                reason="Guest cannot run elevated-risk tools",
                risk_level=inferred_risk,
            )

        return PolicyDecision(
            result="CONTINUE",
            reason="Policy checks passed",
            risk_level=inferred_risk,
        )


def _is_parser_format_recovery(reasoning_result: dict[str, Any]) -> bool:
    """JSON repair/fallback confidence is operational, not a reason to block the user."""
    structured = reasoning_result.get("structured") or {}
    if not isinstance(structured, dict):
        return False
    return bool(structured.get("parser_fallback") or structured.get("parser_repaired"))


def _infer_risk(
    reasoning_result: Optional[dict[str, Any]],
    tool_results: Optional[list[dict[str, Any]]],
) -> str:
    if reasoning_result and reasoning_result.get("risk_level"):
        return str(reasoning_result["risk_level"]).upper()
    if tool_results:
        for item in tool_results:
            if str(item.get("risk_level", "")).upper() in ("HIGH", "CRITICAL"):
                return str(item["risk_level"]).upper()
    return "LOW"


def _has_high_risk_tools(tool_results: Optional[list[dict[str, Any]]]) -> bool:
    if not tool_results:
        return False
    return any(str(r.get("risk_level", "")).upper() in ("HIGH", "CRITICAL") for r in tool_results)


_engine: PolicyEngine | None = None


def get_policy_engine() -> PolicyEngine:
    global _engine
    if _engine is None:
        _engine = PolicyEngine()
    return _engine
