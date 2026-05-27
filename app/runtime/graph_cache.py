"""Graph compilation cache keyed by settings hash (config hot reload)."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from typing import Any, Callable

from app.config.settings import settings

_GRAPH_HASH_KEYS = (
    "REFLECTION_ENABLED",
    "REFLECTION_WRITING_ONLY",
    "OUTPUT_GUARD_ENABLED",
    "OUTPUT_GUARD_LLM_REVIEW",
    "MAX_RETRY_COUNT",
    "SKIP_RETRIEVAL_WHEN_NO_TOOLS",
    "FAST_REASONING_ENABLED",
    "MCP_ENABLED",
    "MISSION_LLM_DECIDE",
    "REASONING_MODE",
)


def settings_graph_hash() -> str:
    payload = {k: getattr(settings, k, None) for k in _GRAPH_HASH_KEYS}
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def cached_graph_compiler(
    builder: Callable[[], Any],
    *,
    compile_fn: Callable[[Any], Any],
) -> Callable[[], Any]:
    """Return get_compiled_* function with hash-based LRU cache."""

    @lru_cache(maxsize=4)
    def _compiled(config_hash: str) -> Any:
        _ = config_hash
        workflow = builder()
        return compile_fn(workflow)

    def get_compiled() -> Any:
        return _compiled(settings_graph_hash())

    get_compiled.cache_clear = _compiled.cache_clear  # type: ignore[attr-defined]
    return get_compiled


def clear_all_graph_caches() -> None:
    from app.runtime.exploration_graph import get_compiled_exploration_graph
    from app.runtime.graph import get_compiled_graph
    from app.runtime.mission_graph import get_compiled_mission_graph
    from app.runtime.supervisor_graph import get_compiled_supervisor_graph
    from app.runtime.worker_graph import get_compiled_worker_graph

    for fn in (
        get_compiled_graph,
        get_compiled_mission_graph,
        get_compiled_supervisor_graph,
        get_compiled_exploration_graph,
        get_compiled_worker_graph,
    ):
        if hasattr(fn, "cache_clear"):
            fn.cache_clear()
