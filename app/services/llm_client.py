from __future__ import annotations

import json
import re
import time
from functools import lru_cache, wraps
from hashlib import sha256
from typing import Any, Callable, Iterator, TypeVar

from app.config.settings import settings
from app.services.langsmith_setup import runnable_config_with_trace

if False:  # TYPE_CHECKING
    from app.services.resource_budget import BudgetContext

F = TypeVar("F", bound=Callable[..., Any])
_STREAM_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_STRUCTURED_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


class RetryableError(Exception):
    """Errors that can be retried (timeouts, rate limits)."""


def with_retry(max_retries: int | None = None, base_delay: float | None = None, backoff: float = 2.0) -> Callable[[F], F]:
    retries = max_retries if max_retries is not None else settings.MODEL_MAX_RETRIES
    delay_base = base_delay if base_delay is not None else settings.LLM_RETRY_BASE_DELAY

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            from app.services.circuit_breaker import (
                CircuitOpenError,
                classify_llm_error,
                get_llm_circuit_breaker,
                is_retryable_category,
            )

            breaker = (
                get_llm_circuit_breaker("default")
                if settings.LLM_CIRCUIT_BREAKER_ENABLED
                else None
            )
            if breaker is not None:
                breaker.allow_call()

            last_error: Exception | None = None
            for attempt in range(retries):
                try:
                    result = func(*args, **kwargs)
                    if breaker is not None:
                        breaker.record_success()
                    return result
                except CircuitOpenError:
                    raise
                except RetryableError as exc:
                    last_error = exc
                    if breaker is not None:
                        breaker.record_failure()
                    if attempt == retries - 1:
                        raise
                    delay = delay_base * (backoff ** attempt)
                    time.sleep(delay)
                except Exception as exc:
                    category = classify_llm_error(exc)
                    from app.services.metrics_service import get_metrics_service

                    get_metrics_service().inc_llm_error(category.value)
                    if breaker is not None:
                        if is_retryable_category(category):
                            breaker.record_failure()
                        else:
                            breaker.record_success()
                    if category.value in ("auth_error", "content_filter", "context_too_long"):
                        raise
                    message = str(exc).lower()
                    if "timeout" in message or "rate" in message or "529" in message or "503" in message:
                        last_error = RetryableError(str(exc))
                        if attempt == retries - 1:
                            raise last_error from exc
                        if breaker is not None:
                            breaker.record_failure()
                        delay = delay_base * (backoff ** attempt)
                        time.sleep(delay)
                        continue
                    raise
            if last_error:
                raise last_error
            raise RuntimeError("retry loop exited unexpectedly")

        return wrapper  # type: ignore[return-value]

    return decorator


def _strip_markdown_fences(text: str) -> str:
    # Accept any fenced code block language tag (json/cpp/python/...)
    fenced = re.search(r"```(?:[a-zA-Z0-9_+-]+)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    return text.strip()


PLANNING_PLAN_MAX_STEPS = 12
_APPEND_STEP_RE = re.compile(r"append|续写|chapter|body", re.IGNORECASE)


def _is_repetitive_writing_step(label: str) -> bool:
    return bool(_APPEND_STEP_RE.search(label))


def normalize_planning_plan(plan: list[Any], *, max_steps: int = PLANNING_PLAN_MAX_STEPS) -> list[str]:
    """Collapse duplicate append steps and cap plan length (models often over-enumerate chapters)."""
    steps: list[str] = []
    for item in plan:
        text = str(item).strip()
        if not text:
            continue
        if steps and _is_repetitive_writing_step(text) and _is_repetitive_writing_step(steps[-1]):
            continue
        steps.append(text)
    if len(steps) <= max_steps:
        return steps
    head = steps[:2]
    tail = steps[-1:] if len(steps) > 3 else []
    middle = [s for s in steps[2:-1] if not _is_repetitive_writing_step(s)]
    merged = head + middle + tail
    if len(merged) > max_steps:
        merged = merged[:max_steps]
    if len(merged) < 2 and steps:
        merged = [steps[0], "append body via writing/mission"]
    return merged


def _extract_plan_strings_from_partial(text: str) -> list[str]:
    """Best-effort plan[] extraction when model output was truncated mid-JSON."""
    anchor = re.search(r'"plan"\s*:\s*\[', text)
    if not anchor:
        return []
    rest = text[anchor.end() :]
    steps: list[str] = []
    for match in re.finditer(r'"((?:[^"\\]|\\.)*)"', rest):
        steps.append(match.group(1))
        if len(steps) >= 64:
            break
    return steps


def _repair_truncated_planning_json(text: str) -> dict[str, Any] | None:
    """Recover planning JSON when the model hit the output token cap mid-object."""
    cleaned = _strip_markdown_fences(text)
    if '"plan"' not in cleaned:
        return None

    open_brackets = max(0, cleaned.count("[") - cleaned.count("]"))
    open_braces = max(0, cleaned.count("{") - cleaned.count("}"))
    suffixes = [
        '"]' + '}' * open_braces,
        ']' * open_brackets + '}' * open_braces,
        '"}',
        '"]}',
        '"' + ']' * open_brackets + '}' * open_braces,
    ]
    for suffix in suffixes:
        try:
            obj = json.loads(cleaned + suffix)
            if isinstance(obj, dict) and isinstance(obj.get("plan"), list):
                obj["plan"] = normalize_planning_plan(obj["plan"])
                return obj
        except json.JSONDecodeError:
            continue

    steps = _extract_plan_strings_from_partial(cleaned)
    if not steps:
        return None

    repaired: dict[str, Any] = {
        "plan": normalize_planning_plan(steps),
        "mission_recommended": True,
    }
    for key in ("risk_level", "skip_retrieval", "mission_recommended", "use_mission"):
        match = re.search(
            rf'"{key}"\s*:\s*(true|false|null|"[^"]*")',
            cleaned,
            re.IGNORECASE,
        )
        if not match:
            continue
        raw = match.group(1).lower()
        if raw == "true":
            repaired[key] = True
        elif raw == "false":
            repaired[key] = False
        elif raw.startswith('"'):
            repaired[key] = match.group(1).strip('"')
    if repaired.get("mission_recommended") or repaired.get("use_mission"):
        repaired["use_mission"] = True
    return repaired


def _iter_json_objects(text: str) -> list[dict[str, Any]]:
    """Collect all top-level JSON objects from model output (in order)."""
    cleaned = _strip_markdown_fences(text)
    decoder = json.JSONDecoder()
    found: list[dict[str, Any]] = []

    for idx, ch in enumerate(cleaned):
        if ch != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(cleaned, idx)
            if isinstance(obj, dict):
                found.append(obj)
        except json.JSONDecodeError:
            continue

    if found:
        return found

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return [parsed]
    except json.JSONDecodeError:
        pass

    for match in re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", cleaned, re.DOTALL):
        try:
            obj, _end = decoder.raw_decode(cleaned, match.start())
            if isinstance(obj, dict):
                found.append(obj)
        except json.JSONDecodeError:
            continue
    return found


_NESTED_MERGE_KEYS = frozenset({"structured", "writing_intent", "mission", "tool_params"})


def _merge_json_objects(
    objects: list[dict[str, Any]],
    *,
    prefer_keys: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Merge multiple JSON blobs; prefer the longest non-empty value for prefer_keys."""
    if not objects:
        return {}
    merged = dict(objects[0])
    for obj in objects[1:]:
        for key, val in obj.items():
            current = merged.get(key)
            if current in (None, "", [], {}):
                merged[key] = val
            elif key in _NESTED_MERGE_KEYS and isinstance(current, dict) and isinstance(val, dict):
                merged[key] = {**current, **val}
    for pk in prefer_keys:
        candidates = [o for o in objects if str(o.get(pk) or "").strip()]
        if candidates:
            best = max(candidates, key=lambda o: len(str(o.get(pk) or "")))
            merged[pk] = best[pk]
    return merged


def _extract_json(text: str, *, prefer_keys: tuple[str, ...] | None = None) -> dict[str, Any]:
    """
    Parse JSON object(s) from model output.

    Handles markdown fences, trailing prose, and multiple JSON blobs
    (common with proxy/wrapper APIs that append thinking chunks before the answer).

    When prefer_keys is set (e.g. ``("summary",)`` for reasoning), merge all
    parsed objects and keep the richest value for those keys instead of only
    using the first object (which may be metadata-only).
    """
    objects = _iter_json_objects(text)
    if objects:
        merged = (
            _merge_json_objects(objects, prefer_keys=prefer_keys)
            if prefer_keys
            else objects[0]
        )
        if isinstance(merged.get("plan"), list):
            merged["plan"] = normalize_planning_plan(merged["plan"])
        return merged

    cleaned = _strip_markdown_fences(text)
    if prefer_keys and "plan" in prefer_keys:
        repaired = _repair_truncated_planning_json(cleaned)
        if repaired:
            return repaired

    raise ValueError(f"Could not parse JSON from model output: {cleaned[:200]}")


def _reasoning_fallback_payload(text: str) -> dict[str, Any]:
    cleaned = _strip_markdown_fences(text)
    return {
        "summary": cleaned[:12000],
        "confidence": 0.45,
        "risk_level": "MEDIUM",
        "structured": {
            "parser_fallback": True,
            "fallback_reason": "non_json_reasoning_output",
        },
    }


def _attempt_reasoning_json_repair(
    malformed_text: str,
    *,
    trace_state: Any | None = None,
    budget_ctx: Any | None = None,
) -> dict[str, Any] | None:
    """
    Ask model to convert malformed reasoning output into valid JSON schema.
    Long-term guardrail: prefer protocol repair before fallback/dead-letter.
    """
    llm = get_llm("reasoning", budget_ctx=budget_ctx)
    if llm is None:
        return None

    from langchain_core.messages import HumanMessage, SystemMessage

    repair_system = (
        "Convert the assistant output into ONE valid JSON object only.\n"
        'Required keys: "summary" (string), "confidence" (0-1 float), '
        '"risk_level" (LOW|MEDIUM|HIGH|CRITICAL), "structured" (object).\n'
        "Do not use markdown fences in the JSON.\n"
        "Executable source code → structured.artifacts: "
        '[{"kind":"code","language":"cpp|python|...","content":"..."}] '
        "with exact newlines/indentation in content; summary may briefly describe the code."
    )
    repair_user = json.dumps(
        {
            "assistant_output": (malformed_text or "")[:20000],
            "schema": {
                "summary": "string",
                "confidence": 0.7,
                "risk_level": "LOW|MEDIUM|HIGH|CRITICAL",
                "structured": {"source": "repair"},
            },
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
        normalized = _normalize_content(content)
        parsed = _extract_json(normalized, prefer_keys=("summary",))
        if not str(parsed.get("summary") or "").strip():
            return None
        return parsed
    except Exception:
        return None


def extract_json_with_repair(
    purpose: str,
    text: str,
    *,
    prefer_keys: tuple[str, ...] | None = None,
    trace_state: Any | None = None,
    budget_ctx: Any | None = None,
) -> dict[str, Any]:
    """Parse JSON with purpose-aware repair/fallback strategy."""
    from app.services.metrics_service import get_metrics_service

    try:
        parsed = _extract_json(text, prefer_keys=prefer_keys)
        if purpose == "planning":
            get_metrics_service().inc_contract_event("planning_parse_ok")
        return parsed
    except ValueError:
        if purpose == "planning":
            repaired = _repair_truncated_planning_json(text)
            if repaired is not None:
                repaired["contract_phase"] = "planning"
                repaired["contract_repaired"] = True
                get_metrics_service().inc_contract_event("planning_parse_repaired")
                return repaired
            get_metrics_service().inc_contract_event("planning_parse_failed")
            raise
        if purpose != "reasoning":
            raise
        repaired = _attempt_reasoning_json_repair(
            text,
            trace_state=trace_state,
            budget_ctx=budget_ctx,
        )
        if repaired is not None:
            repaired["structured"] = {
                **(repaired.get("structured") or {}),
                "parser_repaired": True,
            }
            get_metrics_service().inc_reasoning_parser_event("repaired")
            return repaired
        get_metrics_service().inc_reasoning_parser_event("fallback")
        return _reasoning_fallback_payload(text)


def _prefer_keys_for_purpose(purpose: str) -> tuple[str, ...] | None:
    if purpose == "reasoning":
        return ("summary",)
    if purpose == "planning":
        return ("plan", "writing_intent", "mission")
    return None


def _normalize_anthropic_base_url(base_url: str) -> str:
    """
    ChatAnthropic appends /v1/messages; strip it if config already points at that path.
    Direct HTTP probes use the full .../v1/messages URL unchanged.
    """
    url = (base_url or "").strip().rstrip("/")
    suffix = "/v1/messages"
    if url.endswith(suffix):
        return url[: -len(suffix)] or url
    return url


def _max_tokens_for_purpose(purpose: str) -> int:
    mapping = {
        "planning": settings.MODEL_MAX_TOKENS_PLANNING,
        "reasoning": settings.MODEL_MAX_TOKENS_REASONING,
        "routing": settings.MODEL_MAX_TOKENS_ROUTING,
        "session_turn": settings.MODEL_MAX_TOKENS_ROUTING,
        "writing": settings.MODEL_MAX_TOKENS_WRITING,
    }
    return mapping.get(purpose, settings.MODEL_MAX_TOKENS)


def _resolve_model_name(budget_ctx: Any | None = None) -> str:
    if budget_ctx is not None and getattr(budget_ctx, "model_downgrade", False):
        fallback = str(getattr(settings, "MODEL_FALLBACK_NAME", "")).strip()
        if fallback:
            return fallback
    return settings.MODEL_NAME


@lru_cache(maxsize=16)
def _get_llm_cached(purpose: str, model_name: str, max_tokens: int) -> Any:
    if not settings.MODEL_ENABLED:
        return None
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=model_name,
        api_key=settings.MODEL_API_KEY,
        base_url=_normalize_anthropic_base_url(settings.MODEL_BASE_URL) or None,
        max_tokens=max_tokens,
        temperature=settings.MODEL_TEMPERATURE,
        max_retries=settings.MODEL_MAX_RETRIES,
        timeout=settings.MODEL_TIMEOUT,
        streaming=True,
    )


def get_llm(purpose: str = "default", *, budget_ctx: Any | None = None) -> Any:
    if not settings.MODEL_ENABLED:
        return None
    model_name = _resolve_model_name(budget_ctx)
    max_tokens = _max_tokens_for_purpose(purpose)
    if budget_ctx is not None and getattr(budget_ctx, "model_downgrade", False):
        max_tokens = min(max_tokens, int(getattr(settings, "BUDGET_DOWNGRADE_MAX_TOKENS", 2048)))
    return _get_llm_cached(purpose, model_name, max_tokens)


# Backward compat for tests that clear LLM cache
get_llm.cache_clear = _get_llm_cached.cache_clear  # type: ignore[attr-defined]


def _normalize_content(content: Any) -> str:
    from app.services.llm_gateway import extract_chunk_stream_parts, normalize_message_content

    _thinking, text = extract_chunk_stream_parts(content)
    if text:
        return text
    normalized, _meta = normalize_message_content(content)
    return normalized if normalized else json.dumps(content, ensure_ascii=False) if content is not None else ""


def _cache_key(purpose: str, system_prompt: str, user_content: str) -> str:
    raw = json.dumps(
        {
            "purpose": purpose,
            "system_prompt": system_prompt,
            "user_content": user_content,
            "model": settings.MODEL_NAME,
            "enabled": settings.MODEL_ENABLED,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return sha256(raw.encode("utf-8")).hexdigest()


def _cache_ttl_seconds() -> int:
    return 300


def _get_cached(cache: dict[str, tuple[float, dict[str, Any]]], key: str) -> dict[str, Any] | None:
    item = cache.get(key)
    if not item:
        return None
    expires_at, payload = item
    if time.time() > expires_at:
        cache.pop(key, None)
        return None
    return dict(payload)


def _set_cached(cache: dict[str, tuple[float, dict[str, Any]]], key: str, payload: dict[str, Any]) -> dict[str, Any]:
    cache[key] = (time.time() + _cache_ttl_seconds(), dict(payload))
    return dict(payload)


def _usage_identity(trace_state: Any | None) -> tuple[str, str]:
    from app.services.tenant_context import get_tenant_id

    if isinstance(trace_state, dict):
        tenant_id = str(trace_state.get("tenant_id") or get_tenant_id() or "default")
        user_id = str(trace_state.get("user_id") or "anonymous")
        return tenant_id, user_id
    return get_tenant_id() or "default", "anonymous"


def _record_llm_usage(
    *,
    purpose: str,
    system_prompt: str,
    user_content: str,
    response_text: str,
    trace_state: Any | None = None,
) -> None:
    from app.config.settings import settings
    from app.services.metrics_service import get_metrics_service
    from app.services.resource_budget import _estimate_tokens
    from app.services.tenant_quota import record_usage

    tokens = _estimate_tokens(system_prompt + user_content + response_text)
    tenant_id, user_id = _usage_identity(trace_state)
    metrics = get_metrics_service()
    metrics.inc_llm_tokens(purpose, "total", tokens)
    if settings.MULTI_TENANT_ENABLED:
        record_usage(tenant_id, "tokens", tokens)
        metrics.inc_tenant_tokens(tenant_id, tokens)
    cost_per_1k = float(getattr(settings, "COST_PER_1K_TOKENS", 0.0))
    if cost_per_1k > 0:
        metrics.record_llm_cost_usd(
            (tokens / 1000.0) * cost_per_1k,
            tenant_id=tenant_id,
            user_id=user_id,
        )


@with_retry()
def invoke_structured(
    purpose: str,
    system_prompt: str,
    user_content: str,
    *,
    budget_ctx: Any | None = None,
    trace_state: Any | None = None,
) -> dict[str, Any]:
    from app.services.resource_budget import BudgetExceededError

    if budget_ctx is not None:
        budget_ctx.before_invoke(purpose, system_prompt, user_content)

    key = _cache_key(purpose, system_prompt, user_content)
    cached = _get_cached(_STRUCTURED_CACHE, key)
    if cached is not None:
        if budget_ctx is not None:
            budget_ctx.after_invoke(purpose, system_prompt, user_content, json.dumps(cached))
        _record_llm_usage(
            purpose=purpose,
            system_prompt=system_prompt,
            user_content=user_content,
            response_text=json.dumps(cached),
            trace_state=trace_state,
        )
        return cached

    llm = get_llm(purpose, budget_ctx=budget_ctx)
    if llm is None:
        local = _local_structured_response(purpose, user_content)
        if budget_ctx is not None:
            budget_ctx.after_invoke(purpose, system_prompt, user_content, json.dumps(local))
        _record_llm_usage(
            purpose=purpose,
            system_prompt=system_prompt,
            user_content=user_content,
            response_text=json.dumps(local),
            trace_state=trace_state,
        )
        return _set_cached(_STRUCTURED_CACHE, key, local)

    from langchain_core.messages import HumanMessage, SystemMessage
    from app.services.metrics_service import get_metrics_service

    metrics = get_metrics_service()
    model_name = _resolve_model_name(budget_ctx)
    run_config = runnable_config_with_trace(trace_state if isinstance(trace_state, dict) else None)
    try:
        response = llm.invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_content)],
            config=run_config or None,
        )
        content = response.content if hasattr(response, "content") else str(response)
        normalized = _normalize_content(content)
        if budget_ctx is not None:
            budget_ctx.after_invoke(purpose, system_prompt, user_content, normalized)
        metrics.inc_llm_invoke(purpose, model_name, "ok")
        _record_llm_usage(
            purpose=purpose,
            system_prompt=system_prompt,
            user_content=user_content,
            response_text=normalized,
            trace_state=trace_state,
        )
        parsed = extract_json_with_repair(
            purpose,
            normalized,
            prefer_keys=_prefer_keys_for_purpose(purpose),
            trace_state=trace_state,
            budget_ctx=budget_ctx,
        )
        return _set_cached(_STRUCTURED_CACHE, key, parsed)
    except BudgetExceededError:
        metrics.inc_budget_exhausted(purpose)
        raise
    except Exception as exc:
        metrics.inc_llm_invoke(purpose, model_name, "error")
        message = str(exc).lower()
        if "timeout" in message or "rate" in message or "529" in message or "503" in message:
            raise RetryableError(str(exc)) from exc
        raise


def stream_structured(
    purpose: str,
    system_prompt: str,
    user_content: str,
    *,
    budget_ctx: Any | None = None,
    trace_state: Any | None = None,
    stream_node: str = "",
    stream_phase: str = "",
) -> Iterator[str]:
    from app.services.resource_budget import BudgetExceededError

    if budget_ctx is not None:
        budget_ctx.before_invoke(purpose, system_prompt, user_content)

    key = _cache_key(purpose, system_prompt, user_content)
    cached = _get_cached(_STREAM_CACHE, key)
    if cached is not None:
        text = str(cached.get("text", ""))
        if budget_ctx is not None and text:
            budget_ctx.after_invoke(purpose, system_prompt, user_content, text)
        if text:
            yield text
        return

    llm = get_llm(purpose, budget_ctx=budget_ctx)
    if llm is None:
        local = _local_structured_response(purpose, user_content)
        text = json.dumps(local, ensure_ascii=False)
        if budget_ctx is not None:
            budget_ctx.after_invoke(purpose, system_prompt, user_content, text)
        _set_cached(_STREAM_CACHE, key, {"text": text})
        yield text
        return

    from langchain_core.messages import HumanMessage, SystemMessage
    from app.services.llm_gateway import extract_chunk_stream_parts
    from app.services.stream_progress import report_thinking_delta

    run_config = runnable_config_with_trace(trace_state if isinstance(trace_state, dict) else None)
    chunks: list[str] = []
    from app.services.reasoning_trace import thinking_stream_enabled

    emit_thinking = bool(thinking_stream_enabled() and stream_node)
    try:
        for chunk in llm.stream(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_content)],
            config=run_config or None,
        ):
            raw = getattr(chunk, "content", "")
            thinking_part, text_part = extract_chunk_stream_parts(raw)
            if emit_thinking and thinking_part:
                report_thinking_delta(
                    node=stream_node,
                    phase=stream_phase or f"{purpose}_llm",
                    text=thinking_part,
                )
            if not text_part:
                continue
            chunks.append(text_part)
            yield text_part
    except BudgetExceededError:
        raise
    except Exception as exc:
        message = str(exc).lower()
        if "timeout" in message or "rate" in message or "529" in message or "503" in message:
            raise RetryableError(str(exc)) from exc
        raise

    full_text = "".join(chunks)
    if full_text:
        if budget_ctx is not None:
            budget_ctx.after_invoke(purpose, system_prompt, user_content, full_text)
        _record_llm_usage(
            purpose=purpose,
            system_prompt=system_prompt,
            user_content=user_content,
            response_text=full_text,
            trace_state=trace_state,
        )
        _set_cached(_STREAM_CACHE, key, {"text": full_text})


def _local_structured_response(purpose: str, user_content: str) -> dict[str, Any]:
    """Rule-based structured output when MODEL is disabled (not fake API data)."""
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(user_content)
    except json.JSONDecodeError:
        payload = {"goal": user_content, "raw": user_content}

    goal = str(payload.get("goal") or payload.get("query") or payload.get("question") or user_content)
    risk = str(payload.get("risk_level", "LOW")).upper()

    if purpose == "planning":
        steps = ["analyze_goal", "retrieve_context", "execute_tools", "reason_and_answer"]
        tools: list[str] = []
        if payload.get("use_tools", True):
            tools.append("echo")
        if payload.get("needs_search", True):
            steps.insert(1, "search_knowledge")
        return {
            "plan": steps,
            "selected_tools": tools,
            "risk_level": risk,
        }

    if purpose == "reflection":
        reasoning = payload.get("reasoning_result") or {}
        structured = reasoning.get("structured") or {}
        warnings = list(structured.get("fact_warnings") or [])
        confidence = float(reasoning.get("confidence", 1.0))
        retry = bool(warnings) or confidence < 0.6
        return {
            "critique": "fact alignment issues" if warnings else "acceptable",
            "retry_reasoning": retry,
            "issues": warnings[:5],
            "suggested_fixes": ["reconcile with turn_facts"] if retry else [],
        }

    if purpose == "session_turn":
        msg = str(
            payload.get("user_message")
            or payload.get("goal")
            or payload.get("query")
            or user_content
        )
        from app.services.route_audit.inference import infer_goal_kind_from_text
        from app.services.session.config import load_session_turn_policy_config

        turn_cfg = load_session_turn_policy_config()
        inference = infer_goal_kind_from_text(msg)
        primary = str(inference.get("primary_kind") or "general")
        confidence = float(inference.get("confidence") or 0.0)
        if confidence >= turn_cfg.min_kind_confidence and primary in turn_cfg.resume_on_kinds:
            return {
                "turn_intent": "resume_writing",
                "confidence": confidence,
                "reason": f"local pattern kind {primary}",
            }
        if confidence >= turn_cfg.min_kind_confidence and primary in turn_cfg.isolate_on_kinds:
            return {
                "turn_intent": "qa_side_turn",
                "confidence": confidence,
                "reason": f"local pattern kind {primary}",
            }
        return {
            "turn_intent": "qa_side_turn",
            "confidence": 0.0,
            "reason": "local default suspend",
        }

    if purpose == "routing":
        hint = payload.get("mission_control_hint") or {}
        if hint.get("done") and hint.get("action") == "finish":
            return {
                "action": "finish",
                "next_executor": "pipeline:request",
                "params": {},
                "rationale": hint.get("reason", "success criteria met"),
            }
        if hint.get("done") and hint.get("action") == "pause":
            return {
                "action": "pause",
                "next_executor": "pipeline:request",
                "params": {},
                "rationale": hint.get("reason", "mission paused"),
            }
        return {
            "action": "continue",
            "next_executor": "pipeline:request",
            "params": {},
            "rationale": hint.get("reason", "continue mission"),
        }

    summary = f"Processed goal: {goal}"
    return {
        "summary": summary,
        "confidence": 0.75 if risk == "LOW" else 0.55,
        "risk_level": risk,
        "structured": {"goal": goal, "source": "local_reasoner"},
    }
