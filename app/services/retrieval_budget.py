"""Link retrieval evidence token budget to context governance bucket caps (P1-2)."""

from __future__ import annotations

from typing import Any

from app.config.settings import settings
from app.services.context_policy import (
    get_prompt_context_policy,
    resolve_purpose_for_state,
    scale_policy_for_budget,
)
from app.services.resource_budget import resolve_prompt_token_budget


def resolve_evidence_token_budget_for_state(
    state: dict[str, Any] | None,
    *,
    purpose: str | None = None,
) -> int:
    """
    Derive evidence pipeline token budget from retrieved_knowledge bucket cap
    when ``RETRIEVAL_BUDGET_LINK_CONTEXT`` is enabled.
    """
    fallback = int(getattr(settings, "RETRIEVAL_EVIDENCE_TOKEN_BUDGET", 4000) or 4000)
    if not getattr(settings, "RETRIEVAL_BUDGET_LINK_CONTEXT", False):
        return fallback
    if not state:
        return fallback

    ctx_purpose = purpose or resolve_purpose_for_state(state, "reasoning")
    policy = get_prompt_context_policy(ctx_purpose)
    prompt_budget = resolve_prompt_token_budget(
        state,
        policy_default=policy.default_token_budget,
        purpose=ctx_purpose,
    )
    policy = scale_policy_for_budget(policy, prompt_budget)
    cap = int(policy.bucket_max_tokens.get("retrieved_knowledge", 0) or 0)
    return cap if cap > 0 else fallback
