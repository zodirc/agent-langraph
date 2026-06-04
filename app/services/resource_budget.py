"""
Per-task token/cost budget tracking and enforcement (Ch16).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.config.settings import settings
from app.runtime.state import AgentState, merge_state


class BudgetExceededError(Exception):
    """Raised when a task exceeds its token or cost budget."""


def _estimate_tokens(text: str) -> int:
    """Heuristic: ~4 characters per token for mixed CN/EN text."""
    return max(1, len(text) // 4)


@dataclass
class BudgetContext:
    """Mutable budget tracker for one task invocation."""

    token_limit: int = 0
    cost_limit: float = 0.0
    tokens_used: int = 0
    cost_used: float = 0.0
    exhausted: bool = False
    model_downgrade: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_limit": self.token_limit,
            "cost_limit": self.cost_limit,
            "tokens_used": self.tokens_used,
            "cost_used": round(self.cost_used, 6),
            "exhausted": self.exhausted,
            "model_downgrade": self.model_downgrade,
        }

    @classmethod
    def from_state(cls, state: AgentState) -> BudgetContext:
        raw = state.get("token_budget") or {}
        cost_raw = state.get("cost_budget") or {}
        ctx = cls(
            token_limit=int(raw.get("limit") or 0),
            cost_limit=float(cost_raw.get("limit") or 0.0),
            tokens_used=int(raw.get("used") or 0),
            cost_used=float(cost_raw.get("used") or 0.0),
            exhausted=bool(raw.get("exhausted")),
            model_downgrade=bool(raw.get("model_downgrade")),
        )
        return ctx

    def apply_to_state(self, state: AgentState) -> AgentState:
        blob = self.to_dict()
        return merge_state(
            state,
            token_budget={
                "limit": blob["token_limit"],
                "used": blob["tokens_used"],
                "exhausted": blob["exhausted"],
                "model_downgrade": blob["model_downgrade"],
            },
            cost_budget={
                "limit": blob["cost_limit"],
                "used": blob["cost_used"],
                "exhausted": blob["exhausted"],
            },
        )

    def _usage_ratio(self) -> float:
        ratios: list[float] = []
        if self.token_limit > 0:
            ratios.append(self.tokens_used / self.token_limit)
        if self.cost_limit > 0:
            ratios.append(self.cost_used / self.cost_limit)
        return max(ratios) if ratios else 0.0

    def before_invoke(self, purpose: str, system_prompt: str, user_content: str) -> None:
        if self.exhausted:
            raise BudgetExceededError("Task budget already exhausted")
        estimate = _estimate_tokens(system_prompt + user_content)
        if self.token_limit > 0 and self.tokens_used + estimate > self.token_limit:
            self.exhausted = True
            raise BudgetExceededError(
                f"Token budget exceeded ({self.tokens_used + estimate} > {self.token_limit})"
            )
        threshold = float(getattr(settings, "BUDGET_DOWNGRADE_RATIO", 0.8))
        projected = self.tokens_used + estimate
        if self.token_limit > 0 and projected >= self.token_limit * threshold:
            self.model_downgrade = True

    def after_invoke(
        self,
        purpose: str,
        system_prompt: str,
        user_content: str,
        response_text: str,
    ) -> None:
        used = _estimate_tokens(system_prompt + user_content + response_text)
        self.tokens_used += used
        cost_per_1k = float(getattr(settings, "COST_PER_1K_TOKENS", 0.0))
        if cost_per_1k > 0:
            self.cost_used += (used / 1000.0) * cost_per_1k
        if self.token_limit > 0 and self.tokens_used >= self.token_limit:
            self.exhausted = True
        if self.cost_limit > 0 and self.cost_used >= self.cost_limit:
            self.exhausted = True


def init_task_budget(state: AgentState) -> AgentState:
    """Initialize per-task budgets from settings or input_payload overrides."""
    payload = state.get("input_payload") or {}
    token_limit = int(
        payload.get("token_budget")
        or getattr(settings, "DEFAULT_TOKEN_BUDGET", 0)
    )
    cost_limit = float(
        payload.get("cost_budget")
        or getattr(settings, "DEFAULT_COST_BUDGET", 0.0)
    )
    ctx = BudgetContext(token_limit=token_limit, cost_limit=cost_limit)
    return ctx.apply_to_state(state)


def budget_context_from_state(state: AgentState) -> BudgetContext:
    return BudgetContext.from_state(state)


def resolve_prompt_token_budget(
    state: AgentState | dict[str, Any],
    *,
    policy_default: int,
) -> int:
    """
    Unify task BudgetContext with Context Governance envelope budget (ADR §14).
    """
    from app.config.settings import settings

    ctx = BudgetContext.from_state(state)  # type: ignore[arg-type]
    gov_default = int(getattr(settings, "CONTEXT_GOVERNANCE_DEFAULT_BUDGET", 0) or 0)
    limit = ctx.token_limit or gov_default or policy_default
    if limit <= 0:
        return policy_default
    remaining = max(512, limit - ctx.tokens_used)
    return min(policy_default, remaining)
