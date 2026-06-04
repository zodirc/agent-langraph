"""Session token metering — provider usage preferred, local counting as Copilot-style fallback."""

from __future__ import annotations

from typing import Any

from app.config.settings import settings
from app.services.model_catalog import resolve_catalog_entry


def resolve_session_model(state: dict[str, Any]) -> tuple[str, str | None]:
    payload = state.get("input_payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    model_id = str(payload.get("chat_model_id") or "").strip() or None
    entry = resolve_catalog_entry(model_id=model_id) if model_id else None
    if entry:
        return entry.model_name, entry.id
    from app.services.llm_client import _resolve_model_name
    from app.services.resource_budget import budget_context_from_state

    try:
        name = _resolve_model_name(budget_context_from_state(state))  # type: ignore[arg-type]
    except Exception:
        name = str(getattr(settings, "MODEL_NAME", "") or "")
    return name, None


def resolve_model_context_window(state: dict[str, Any], model_id: str | None = None) -> int:
    """Model-native context window (plugin-style denominator, e.g. 251.2k)."""
    if model_id:
        entry = resolve_catalog_entry(model_id=model_id)
        if entry and entry.context_window_tokens > 0:
            return entry.context_window_tokens
    _name, mid = resolve_session_model(state)
    if mid:
        entry = resolve_catalog_entry(model_id=mid)
        if entry and entry.context_window_tokens > 0:
            return entry.context_window_tokens
    from app.services.model_catalog import _infer_context_window

    model_name, _ = resolve_session_model(state)
    return _infer_context_window(model_name)


def resolve_session_context_window() -> int:
    """IDE/session cap from config (secondary reference)."""
    explicit = int(getattr(settings, "SESSION_CONTEXT_WINDOW", 0) or 0)
    if explicit > 0:
        return explicit
    gov = int(getattr(settings, "CONTEXT_GOVERNANCE_DEFAULT_BUDGET", 0) or 0)
    if gov > 0:
        return gov
    fallback = int(getattr(settings, "MODEL_CONTEXT_WINDOW", 0) or 0)
    return fallback if fallback > 0 else 128_000


def _usage_blob(tb: dict[str, Any], provider_key: str, local_key: str) -> dict[str, Any]:
    provider = tb.get(provider_key) if isinstance(tb.get(provider_key), dict) else {}
    local = tb.get(local_key) if isinstance(tb.get(local_key), dict) else {}
    return {"provider": provider, "local": local}


def _pick_int(provider: dict[str, Any], local: dict[str, Any], field: str) -> int:
    p = int(provider.get(field) or 0)
    if p > 0:
        return p
    return int(local.get(field) or 0)


def _sum_int_fields(data: dict[str, Any], *fields: str) -> int:
    return sum(int(data.get(field) or 0) for field in fields)


def _context_fill_from_state(
    state: dict[str, Any],
    *,
    last_prompt: int,
    provider_prompt: int,
    local_prompt: int,
) -> tuple[int, str]:
    trace = state.get("trace_context") if isinstance(state.get("trace_context"), dict) else {}
    comp = trace.get("last_context_composition")
    assembly_tokens = 0
    if isinstance(comp, dict):
        assembly_tokens = int(comp.get("assembly_tokens") or 0)

    if provider_prompt > 0:
        return provider_prompt, "provider_prompt"
    if local_prompt > 0:
        return local_prompt, "local_prompt"
    if assembly_tokens > 0:
        return assembly_tokens, "assembly"
    if last_prompt > 0:
        return last_prompt, "last_prompt_fallback"
    return 0, "none"


def read_session_token_totals(state: dict[str, Any]) -> dict[str, Any]:
    tb = state.get("token_budget") or {}
    if not isinstance(tb, dict):
        tb = {}
    used_billed = int(tb.get("used_billed") or 0)
    used_local = int(tb.get("used_local") or 0)
    session_prompt_billed = int(tb.get("session_prompt_tokens_billed") or 0)
    session_completion_billed = int(tb.get("session_completion_tokens_billed") or 0)
    session_prompt_local = int(tb.get("session_prompt_tokens_local") or 0)
    session_completion_local = int(tb.get("session_completion_tokens_local") or 0)
    last = _usage_blob(tb, "last_usage", "last_usage_local")
    prompt = _pick_int(last["provider"], last["local"], "prompt_tokens")
    completion = _pick_int(last["provider"], last["local"], "completion_tokens")
    total_last = _pick_int(last["provider"], last["local"], "total_tokens")
    provider_prompt = int(last["provider"].get("prompt_tokens") or 0)
    provider_completion = int(last["provider"].get("completion_tokens") or 0)
    local_prompt = int(last["local"].get("prompt_tokens") or 0)
    local_completion = int(last["local"].get("completion_tokens") or 0)
    has_provider = used_billed > 0 or bool(last["provider"])
    has_local = used_local > 0 or bool(last["local"])
    session_used = used_billed if used_billed > 0 else used_local
    session_prompt = session_prompt_billed if session_prompt_billed > 0 else session_prompt_local
    session_completion = (
        session_completion_billed
        if session_completion_billed > 0
        else session_completion_local
    )
    context_used, context_source = _context_fill_from_state(
        state,
        last_prompt=prompt,
        provider_prompt=provider_prompt,
        local_prompt=local_prompt,
    )
    return {
        "session_tokens_consumed_billed": used_billed,
        "session_tokens_consumed_local": used_local,
        "session_tokens_consumed": session_used,
        "session_prompt_tokens_consumed": session_prompt,
        "session_completion_tokens_consumed": session_completion,
        "session_prompt_tokens_consumed_billed": session_prompt_billed,
        "session_completion_tokens_consumed_billed": session_completion_billed,
        "session_prompt_tokens_consumed_local": session_prompt_local,
        "session_completion_tokens_consumed_local": session_completion_local,
        "last_prompt_tokens": prompt,
        "last_completion_tokens": completion,
        "last_total_tokens": total_last,
        "last_provider_prompt_tokens": provider_prompt,
        "last_provider_completion_tokens": provider_completion,
        "last_local_prompt_tokens": local_prompt,
        "last_local_completion_tokens": local_completion,
        "last_call_purpose": str(
            last["provider"].get("purpose") or last["local"].get("purpose") or ""
        ),
        "llm_call_count": int(tb.get("llm_call_count") or 0),
        "has_provider_usage": has_provider,
        "has_local_usage": has_local,
        "has_last_call_usage": prompt > 0 or completion > 0 or total_last > 0,
        "billing_source": (
            "provider"
            if used_billed > 0
            else ("local" if has_local else "none")
        ),
        "context_fill_tokens": context_used,
        "context_fill_source": context_source,
        "has_context_snapshot": context_used > 0,
        "session_total_from_parts_billed": _sum_int_fields(
            tb,
            "session_prompt_tokens_billed",
            "session_completion_tokens_billed",
        ),
        "session_total_from_parts_local": _sum_int_fields(
            tb,
            "session_prompt_tokens_local",
            "session_completion_tokens_local",
        ),
    }


def build_session_meter(
    state: dict[str, Any],
    *,
    purpose: str = "reasoning",
    model_id: str | None = None,
) -> dict[str, Any]:
    """Fields for Web session panel (Copilot-style dual metrics)."""
    _ = purpose
    model_name, resolved_id = resolve_session_model(state)
    if model_id:
        entry = resolve_catalog_entry(model_id=model_id)
        if entry:
            model_name = entry.model_name
            resolved_id = entry.id

    totals = read_session_token_totals(state)
    model_window = resolve_model_context_window(state, model_id=resolved_id or model_id)
    session_window = resolve_session_context_window()
    context_used = int(totals["context_fill_tokens"] or 0)
    session_total = int(totals["session_tokens_consumed"] or 0)
    last_total = int(totals["last_total_tokens"] or 0)
    if last_total <= 0:
        last_total = int(totals["last_prompt_tokens"] or 0) + int(
            totals["last_completion_tokens"] or 0
        )

    context_pct = None
    context_avail = None
    if context_used > 0 and model_window > 0:
        context_avail = max(0, model_window - context_used)
        context_pct = min(100, round((context_used / model_window) * 100))

    has_any = totals["has_provider_usage"] or totals["has_local_usage"]
    has_context = bool(totals["has_context_snapshot"])

    return {
        "model_name": model_name,
        "model_id": resolved_id,
        "data_mode": "provider+local" if has_any else "none",
        "billing_source": totals["billing_source"],
        "session_tokens_consumed": session_total if has_any else None,
        "session_tokens_consumed_billed": int(totals["session_tokens_consumed_billed"] or 0),
        "session_tokens_consumed_local": int(totals["session_tokens_consumed_local"] or 0),
        "session_prompt_tokens_consumed": int(totals["session_prompt_tokens_consumed"] or 0),
        "session_completion_tokens_consumed": int(
            totals["session_completion_tokens_consumed"] or 0
        ),
        "session_prompt_tokens_consumed_billed": int(
            totals["session_prompt_tokens_consumed_billed"] or 0
        ),
        "session_completion_tokens_consumed_billed": int(
            totals["session_completion_tokens_consumed_billed"] or 0
        ),
        "session_prompt_tokens_consumed_local": int(
            totals["session_prompt_tokens_consumed_local"] or 0
        ),
        "session_completion_tokens_consumed_local": int(
            totals["session_completion_tokens_consumed_local"] or 0
        ),
        "last_request_tokens": last_total if totals["has_last_call_usage"] else None,
        "llm_call_count": totals["llm_call_count"],
        "last_prompt_tokens": (
            int(totals["last_prompt_tokens"] or 0)
            if totals["has_last_call_usage"]
            else None
        ),
        "last_completion_tokens": (
            int(totals["last_completion_tokens"] or 0)
            if totals["has_last_call_usage"]
            else None
        ),
        "last_provider_prompt_tokens": int(totals["last_provider_prompt_tokens"] or 0),
        "last_provider_completion_tokens": int(
            totals["last_provider_completion_tokens"] or 0
        ),
        "last_local_prompt_tokens": int(totals["last_local_prompt_tokens"] or 0),
        "last_local_completion_tokens": int(totals["last_local_completion_tokens"] or 0),
        "last_call_purpose": totals["last_call_purpose"] or None,
        "model_context_window_tokens": model_window,
        "context_length_used_tokens": context_used if has_context else None,
        "context_length_max_tokens": model_window,
        "context_length_used_percent": context_pct,
        "context_length_available_tokens": context_avail,
        "context_length_source": totals["context_fill_source"],
        "session_context_window_tokens": session_window,
        "context_window_tokens": model_window,
        "context_window_used_tokens": context_used if has_context else None,
        "context_window_available_tokens": context_avail,
        "context_window_used_percent": context_pct,
    }
