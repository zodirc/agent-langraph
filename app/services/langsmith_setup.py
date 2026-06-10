from __future__ import annotations

import json
import os
from typing import Any, Optional

from app.config.settings import settings


def configure_langsmith() -> None:
    """Enable LangSmith tracing when configured (§25.8 / Appendix F)."""
    if not settings.LANGSMITH_ENABLED or not settings.LANGSMITH_API_KEY:
        return
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_API_KEY", settings.LANGSMITH_API_KEY)
    os.environ.setdefault("LANGCHAIN_PROJECT", settings.LANGSMITH_PROJECT)


def langsmith_enabled() -> bool:
    return bool(settings.LANGSMITH_ENABLED and settings.LANGSMITH_API_KEY)


def build_trace_metadata(state: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """
    Metadata attached to LLM runs for LangSmith filtering (Appendix F).
    Safe to pass via RunnableConfig.metadata.
    """
    if not state:
        return {"runtime": "agent-langraph"}
    payload = state.get("input_payload") or {}
    meta: dict[str, Any] = {
        "runtime": "agent-langraph",
        "task_id": state.get("task_id"),
        "session_id": state.get("session_id"),
        "task_type": state.get("task_type"),
        "execution_mode": state.get("execution_mode"),
        "reasoning_mode": state.get("reasoning_mode") or payload.get("reasoning_mode"),
        "policy_result": state.get("policy_result"),
        "trace_show_thinking": settings.REASONING_TRACE_SHOW_THINKING,
    }
    return {k: v for k, v in meta.items() if v is not None}


def runnable_config_with_trace(state: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """LangChain RunnableConfig fragment for invoke/stream calls."""
    if not langsmith_enabled():
        return {}
    return {"metadata": build_trace_metadata(state)}


def export_trace_metadata_env(state: dict[str, Any]) -> None:
    """
    Optional: set LANGCHAIN_METADATA for processes that read env-based tags.
    Prefer runnable_config_with_trace per call.
    """
    if not langsmith_enabled():
        return
    os.environ["LANGCHAIN_METADATA"] = json.dumps(build_trace_metadata(state), ensure_ascii=False)
