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
    tokens_used_estimate: int = 0
    tokens_used_billed: int = 0
    cost_used: float = 0.0
    exhausted: bool = False
    model_downgrade: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_limit": self.token_limit,
            "cost_limit": self.cost_limit,
            "tokens_used": self.tokens_used,
            "tokens_used_estimate": self.tokens_used_estimate,
            "tokens_used_billed": self.tokens_used_billed,
            "cost_used": round(self.cost_used, 6),
            "exhausted": self.exhausted,
            "model_downgrade": self.model_downgrade,
        }

    @classmethod
    def from_state(cls, state: AgentState) -> BudgetContext:
        raw = state.get("token_budget") or {}
        cost_raw = state.get("cost_budget") or {}
        used = int(raw.get("used") or 0)
        ctx = cls(
            token_limit=int(raw.get("limit") or 0),
            cost_limit=float(cost_raw.get("limit") or 0.0),
            tokens_used=used,
            tokens_used_estimate=int(raw.get("used_estimate") or used),
            tokens_used_billed=int(raw.get("used_billed") or 0),
            cost_used=float(cost_raw.get("used") or 0.0),
            exhausted=bool(raw.get("exhausted")),
            model_downgrade=bool(raw.get("model_downgrade")),
        )
        return ctx

    def apply_to_state(self, state: AgentState) -> AgentState:
        blob = self.to_dict()
        prev = state.get("token_budget") if isinstance(state.get("token_budget"), dict) else {}
        token_budget: dict[str, Any] = {
            "limit": blob["token_limit"],
            "used": blob["tokens_used_billed"] or blob["tokens_used"],
            "used_estimate": blob["tokens_used_estimate"],
            "used_billed": blob["tokens_used_billed"],
            "exhausted": blob["exhausted"],
            "model_downgrade": blob["model_downgrade"],
        }
        if prev.get("last_usage"):
            token_budget["last_usage"] = prev["last_usage"]
        if prev.get("last_usage_local"):
            token_budget["last_usage_local"] = prev["last_usage_local"]
        if prev.get("used_local") is not None:
            token_budget["used_local"] = prev["used_local"]
        if prev.get("llm_call_count"):
            token_budget["llm_call_count"] = prev["llm_call_count"]
        return merge_state(
            state,
            token_budget=token_budget,
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
        *,
        billed_tokens: int | None = None,
    ) -> None:
        estimated = _estimate_tokens(system_prompt + user_content + response_text)
        self.tokens_used_estimate += estimated
        if billed_tokens is not None and billed_tokens > 0:
            self.tokens_used_billed += billed_tokens
            self.tokens_used += billed_tokens
        else:
            self.tokens_used += estimated
        cost_per_1k = float(getattr(settings, "COST_PER_1K_TOKENS", 0.0))
        billed_for_cost = billed_tokens if billed_tokens is not None else estimated
        if cost_per_1k > 0:
            self.cost_used += (billed_for_cost / 1000.0) * cost_per_1k
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


def record_session_token_usage(
    state: dict[str, Any],
    *,
    system_prompt: str,
    user_content: str,
    response_text: str,
    billed_tokens: int | None = None,
    usage_detail: dict[str, Any] | None = None,
    purpose: str = "",
    budget_ctx: BudgetContext | None = None,
) -> None:
    """Accumulate provider + local token usage on task state for session panel."""
    from datetime import datetime, timezone

    from app.services.context_meter import resolve_session_model
    from app.services.token_counter import count_llm_exchange_tokens

    prev = state.get("token_budget") if isinstance(state.get("token_budget"), dict) else {}
    if budget_ctx is not None:
        blob = budget_ctx.to_dict()
    else:
        ctx = BudgetContext.from_state(state)  # type: ignore[arg-type]
        ctx.after_invoke(
            "session",
            system_prompt,
            user_content,
            response_text,
            billed_tokens=billed_tokens,
        )
        blob = ctx.to_dict()
    tb: dict[str, Any] = {
        "limit": blob["token_limit"],
        "used": blob["tokens_used_billed"],
        "used_estimate": blob["tokens_used_estimate"],
        "used_billed": blob["tokens_used_billed"],
        "exhausted": blob["exhausted"],
        "model_downgrade": blob["model_downgrade"],
        "llm_call_count": int(prev.get("llm_call_count") or 0),
        "last_usage": dict(prev.get("last_usage") or {}),
        "used_local": int(prev.get("used_local") or 0),
        "last_usage_local": dict(prev.get("last_usage_local") or {}),
        "session_prompt_tokens_billed": int(prev.get("session_prompt_tokens_billed") or 0),
        "session_completion_tokens_billed": int(prev.get("session_completion_tokens_billed") or 0),
        "session_prompt_tokens_local": int(prev.get("session_prompt_tokens_local") or 0),
        "session_completion_tokens_local": int(prev.get("session_completion_tokens_local") or 0),
    }
    model_name, _ = resolve_session_model(state)
    local_detail = count_llm_exchange_tokens(
        model_name=model_name,
        system_prompt=system_prompt,
        user_content=user_content,
        response_text=response_text,
    )
    call_recorded = False
    if int(local_detail.get("total_tokens") or 0) > 0:
        tb["used_local"] = int(tb.get("used_local") or 0) + int(local_detail["total_tokens"])
        tb["session_prompt_tokens_local"] = int(tb.get("session_prompt_tokens_local") or 0) + int(
            local_detail.get("prompt_tokens") or 0
        )
        tb["session_completion_tokens_local"] = int(
            tb.get("session_completion_tokens_local") or 0
        ) + int(local_detail.get("completion_tokens") or 0)
        tb["llm_call_count"] = int(tb["llm_call_count"]) + 1
        call_recorded = True
        tb["last_usage_local"] = {
            "purpose": purpose,
            "prompt_tokens": int(local_detail["prompt_tokens"]),
            "completion_tokens": int(local_detail["completion_tokens"]),
            "total_tokens": int(local_detail["total_tokens"]),
            "source": "local",
            "at": datetime.now(timezone.utc).isoformat(),
        }
    if usage_detail and int(usage_detail.get("total_tokens") or 0) > 0:
        if not call_recorded:
            tb["llm_call_count"] = int(tb["llm_call_count"]) + 1
        tb["session_prompt_tokens_billed"] = int(tb.get("session_prompt_tokens_billed") or 0) + int(
            usage_detail.get("prompt_tokens") or 0
        )
        tb["session_completion_tokens_billed"] = int(
            tb.get("session_completion_tokens_billed") or 0
        ) + int(usage_detail.get("completion_tokens") or 0)
        tb["last_usage"] = {
            "purpose": purpose,
            "prompt_tokens": int(usage_detail.get("prompt_tokens") or 0),
            "completion_tokens": int(usage_detail.get("completion_tokens") or 0),
            "total_tokens": int(usage_detail.get("total_tokens") or 0),
            "source": "provider",
            "at": datetime.now(timezone.utc).isoformat(),
        }
    state["token_budget"] = tb


def _clamp_int(value: int, floor: int, ceiling: int) -> int:
    return max(floor, min(ceiling, value))


def resolve_prompt_token_budget(
    state: AgentState | dict[str, Any],
    *,
    policy_default: int,
    purpose: str = "reasoning",
) -> int:
    """
    Unify task BudgetContext with Context Governance envelope budget (ADR §14).

    When ``CONTEXT_WINDOW_ADAPTIVE_BUDGET`` is enabled, derive budget from the
    model context window instead of the fixed per-purpose policy cap.
    """
    from app.config.settings import settings

    ctx = BudgetContext.from_state(state)  # type: ignore[arg-type]
    gov_default = int(getattr(settings, "CONTEXT_GOVERNANCE_DEFAULT_BUDGET", 0) or 0)
    limit = ctx.token_limit or gov_default or policy_default
    if limit <= 0:
        return policy_default
    remaining = max(512, limit - ctx.tokens_used)

    if not getattr(settings, "CONTEXT_WINDOW_ADAPTIVE_BUDGET", False):
        return min(policy_default, remaining)

    from app.services.context_meter import resolve_model_context_window
    from app.services.llm_client import _max_tokens_for_purpose

    window = resolve_model_context_window(state)
    out_reserve = _max_tokens_for_purpose(purpose)
    utilization = float(getattr(settings, "CONTEXT_WINDOW_UTILIZATION", 0.6))
    floor = int(getattr(settings, "CONTEXT_PROMPT_BUDGET_FLOOR", 12000))
    ceiling = int(getattr(settings, "CONTEXT_PROMPT_BUDGET_CEILING", 160000))

    target = int(window * utilization) - ctx.tokens_used - out_reserve
    budget = _clamp_int(target, floor, ceiling)
    if ctx.token_limit > 0:
        budget = min(budget, remaining)
    return max(512, budget)
