from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from app.services.secrets import secrets_provider


def _bootstrap_dotenv() -> None:
    """Load project .env for local uvicorn; do not override explicit process env."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = Path(__file__).resolve().parents[2]
    env_path = root / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=False)


_bootstrap_dotenv()


def _resolve_env(value: str) -> str:
    """Replace ${VAR} and ${VAR:default} placeholders."""
    pattern = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")

    def replacer(match: re.Match[str]) -> str:
        key = match.group(1)
        default = match.group(2)
        env_val = os.environ.get(key)
        if env_val is not None:
            return env_val
        if default is not None:
            return default
        return ""

    return pattern.sub(replacer, value)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("1", "true", "yes", "on")
    return bool(value)


def _clean_optional_str(value: Any) -> str:
    """Normalize optional config/env strings; treat None/null as empty."""
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in ("none", "null"):
        return ""
    return text


class Settings:
    """Unified configuration loaded from config/config.yaml."""

    def __init__(self, config_path: str | None = None) -> None:
        root = Path(__file__).resolve().parents[2]
        env_config = os.environ.get("CONFIG_PATH", "").strip()
        if config_path:
            path = Path(config_path)
        elif env_config:
            path = Path(env_config)
        else:
            path = root / "config" / "config.yaml"
        raw = self._load_yaml(str(path))

        secrets = secrets_provider()
        self.SECRETS_BACKEND = (
            os.environ.get("SECRETS_BACKEND") or str(raw.get("secrets", {}).get("backend", "env"))
        ).lower()

        from app.llm.registry import (
            default_model_name,
            normalize_provider_id,
            resolve_model_api_key,
            resolve_model_base_url,
        )

        model = raw.get("model", {})
        self.MODEL_PROVIDER = normalize_provider_id(
            os.environ.get("MODEL_PROVIDER") or str(model.get("provider", "anthropic"))
        )
        yaml_api_key = _clean_optional_str(model.get("api_key", ""))
        self.MODEL_API_KEY = resolve_model_api_key(
            self.MODEL_PROVIDER, secrets, yaml_api_key=yaml_api_key
        )
        configured_base = _clean_optional_str(model.get("base_url", ""))
        if os.environ.get("MODEL_BASE_URL"):
            configured_base = _clean_optional_str(os.environ.get("MODEL_BASE_URL", ""))
        self.MODEL_BASE_URL = resolve_model_base_url(self.MODEL_PROVIDER, configured_base)
        self.MODEL_NAME = default_model_name(
            self.MODEL_PROVIDER,
            os.environ.get("MODEL_NAME") or str(model.get("name", "")),
        )
        self.MODEL_ENABLED = _coerce_bool(model.get("enabled", True)) and bool(self.MODEL_API_KEY)
        self.MODEL_TEMPERATURE = float(model.get("temperature", 0))
        self.MODEL_MAX_TOKENS = int(model.get("max_tokens", 4096))
        self.MODEL_TIMEOUT = int(model.get("timeout", 60))
        self.MODEL_MAX_RETRIES = int(model.get("max_retries", 3))
        purpose_tokens = model.get("max_tokens_by_purpose", {})
        if not isinstance(purpose_tokens, dict):
            purpose_tokens = {}
        self.MODEL_MAX_TOKENS_PLANNING = int(
            purpose_tokens.get("planning", min(4096, self.MODEL_MAX_TOKENS))
        )
        self.MODEL_MAX_TOKENS_REASONING = int(
            purpose_tokens.get("reasoning", min(8192, self.MODEL_MAX_TOKENS))
        )
        self.MODEL_MAX_TOKENS_ROUTING = int(
            purpose_tokens.get("routing", min(1024, self.MODEL_MAX_TOKENS))
        )
        self.MODEL_MAX_TOKENS_WRITING = int(
            purpose_tokens.get("writing", min(16384, self.MODEL_MAX_TOKENS))
        )

        performance = raw.get("performance", {})
        self.FAST_REASONING_ENABLED = _coerce_bool(
            performance.get("fast_reasoning_enabled", False)
        )
        self.SKIP_RETRIEVAL_WHEN_NO_TOOLS = _coerce_bool(
            performance.get("skip_retrieval_when_no_tools", True)
        )
        self.STREAM_SAVE_EVERY_NODE = _coerce_bool(
            performance.get("stream_save_every_node", False)
        )
        self.REASONING_TRACE_ENABLED = _coerce_bool(
            performance.get("reasoning_trace_enabled", True)
        )
        self.REASONING_TRACE_VERBOSE = _coerce_bool(
            performance.get("reasoning_trace_verbose", True)
        )
        self.REASONING_TRACE_MAX_CHARS = int(
            performance.get("reasoning_trace_max_chars", 48000)
        )
        self.REASONING_TRACE_PROGRESS_INTERVAL = int(
            performance.get("reasoning_trace_progress_interval", 200)
        )
        self.REASONING_TRACE_SHOW_THINKING = _coerce_bool(
            performance.get("reasoning_trace_show_thinking", False)
        )
        if "thinking_stream_enabled" in performance:
            _thinking_default = performance.get("thinking_stream_enabled")
        else:
            _thinking_default = performance.get("reasoning_trace_show_thinking", True)
        self.THINKING_STREAM_ENABLED = _coerce_bool(_thinking_default)
        self.ANSWER_STREAM_ENABLED = _coerce_bool(
            performance.get("answer_stream_enabled", True)
        )
        self.WRITING_STREAM_ENABLED = _coerce_bool(
            performance.get("writing_stream_enabled", True)
        )
        self.WRITING_STREAM_MAX_CHARS = int(
            performance.get("writing_stream_max_chars", 200_000)
        )
        writing_gen = performance.get("writing_generation", {})
        if not isinstance(writing_gen, dict):
            writing_gen = {}
        self.WRITING_STREAM_MAX_RETRIES = int(writing_gen.get("stream_max_retries", 2))
        self.WRITING_STREAM_MAX_DURATION_SEC = int(
            writing_gen.get("max_stream_duration_sec", 600)
        )
        self.WRITING_STREAM_MAX_ACCUMULATED_CHARS = int(
            writing_gen.get("max_accumulated_chars", 120_000)
        )
        self.WRITING_BUFFER_STALL_CHARS = int(writing_gen.get("buffer_stall_trace_chars", 8000))
        self.WRITING_BUFFER_STALL_PREVIEW_CHARS = int(
            writing_gen.get("buffer_stall_preview_chars", 200)
        )
        self.WRITING_PARTIAL_ON_DISCONNECT = _coerce_bool(
            writing_gen.get("partial_commit_on_disconnect", True)
        )

        self.LLM_RETRY_BASE_DELAY = float(performance.get("llm_retry_base_delay", 0.5))
        self.DB_SAVE_MAX_RETRIES = int(performance.get("db_save_max_retries", 3))
        self.DB_SAVE_RETRY_BASE_DELAY = float(performance.get("db_save_retry_base_delay", 0.05))

        agent_cfg = raw.get("agent", {})
        self.AGENT_PROMPT_EXTRA = str(agent_cfg.get("prompt_extra", "")).strip()

        session_cfg = raw.get("session", {})
        self.SESSION_ENABLED = _coerce_bool(session_cfg.get("enabled", True))
        self.SESSION_ONE_TASK_PER_WINDOW = _coerce_bool(
            session_cfg.get("one_task_per_window", True)
        )
        self.SESSION_MAX_HISTORY_TURNS = int(session_cfg.get("max_history_turns", 40))
        self.SESSION_MAX_HISTORY_CHARS = int(session_cfg.get("max_history_chars", 48000))
        self.SESSION_COMPRESS_ENABLED = _coerce_bool(session_cfg.get("compress_enabled", True))
        self.SESSION_MEMORY_RETRIEVAL_ENABLED = _coerce_bool(
            session_cfg.get("memory_retrieval_enabled", True)
        )
        self.SESSION_CONFIG = session_cfg if isinstance(session_cfg, dict) else {}
        turn_policy_cfg = session_cfg.get("turn_policy") if isinstance(session_cfg, dict) else {}
        self.SESSION_TURN_POLICY_CONFIG = (
            turn_policy_cfg if isinstance(turn_policy_cfg, dict) else {}
        )

        storage = raw.get("storage", {})
        self.STORAGE_BACKEND = (
            os.environ.get("STORAGE_BACKEND") or str(storage.get("backend", "sqlite"))
        ).lower()
        self.POSTGRES_URL = (
            os.environ.get("DATABASE_URL")
            or os.environ.get("POSTGRES_URL")
            or str(storage.get("postgres_url", "")).strip()
        )
        self.POSTGRES_POOL_MAX_SIZE = int(storage.get("postgres_pool_max_size", 10))
        checkpoint_backend = storage.get("checkpoint_backend")
        if checkpoint_backend:
            self.CHECKPOINT_BACKEND = str(checkpoint_backend).lower()
        elif self.STORAGE_BACKEND == "postgres" and self.POSTGRES_URL:
            self.CHECKPOINT_BACKEND = "postgres"
        else:
            self.CHECKPOINT_BACKEND = "sqlite"
        self.SQLITE_PATH = os.environ.get("SQLITE_PATH") or str(
            storage.get("sqlite_path", "./data/db/agent.db")
        )
        self.CHECKPOINT_SQLITE_PATH = os.environ.get("CHECKPOINT_SQLITE_PATH") or str(
            storage.get("checkpoint_sqlite_path", "./data/db/agent_checkpoints.db")
        )
        self.VECTORSTORE_PATH = os.environ.get("VECTORSTORE_PATH") or str(
            storage.get("vectorstore_path", "./data/vectorstore")
        )
        self.LOG_PATH = os.environ.get("LOG_PATH") or str(
            storage.get("log_path", "./data/logs/agent.log")
        )

        app_cfg = raw.get("app", {})
        self.APP_ENV = os.environ.get("APP_ENV") or str(app_cfg.get("env", "development"))
        self.APP_PORT = int(os.environ.get("APP_PORT", app_cfg.get("port", 8000)))
        self.APP_DEBUG = _coerce_bool(app_cfg.get("debug", False))
        self.APP_SECRET_KEY = secrets.get("APP_SECRET_KEY") or str(
            app_cfg.get("secret_key", "dev-secret")
        )

        cli_cfg = raw.get("cli", {})
        self.CLI_API_BASE_URL = str(cli_cfg.get("api_base_url", "http://127.0.0.1:8000"))
        self.CLI_API_KEY = str(cli_cfg.get("api_key", "")).strip()

        knowledge = raw.get("knowledge", {})
        self.KNOWLEDGE_BACKEND = str(knowledge.get("backend", "chroma"))
        self.EMBEDDING_MODEL = str(knowledge.get("embedding_model", "default"))
        self.EMBEDDING_API_KEY = secrets.get("VOYAGE_API_KEY") or str(
            knowledge.get("embedding_api_key", "")
        ).strip()
        self.RETRIEVAL_TOP_K = int(knowledge.get("top_k", 5))
        self.SIMILARITY_THRESHOLD = float(knowledge.get("similarity_threshold", 0.3))
        self.KNOWLEDGE_COLLECTION = str(knowledge.get("collection_name", "agent_knowledge"))
        self.KNOWLEDGE_CONTENT_DIR = (
            os.environ.get("KNOWLEDGE_CONTENT_DIR", "").strip()
            or str(knowledge.get("content_dir", "knowledge")).strip()
            or "knowledge"
        )
        self.QDRANT_URL = str(knowledge.get("qdrant_url", "")).strip()
        self.QDRANT_PATH = str(knowledge.get("qdrant_path", "")).strip()

        rag = raw.get("rag", {})
        if not isinstance(rag, dict):
            rag = {}
        self.RAG_RERANK_ENABLED = _coerce_bool(rag.get("rerank_enabled", False))
        self.RAG_RERANK_CANDIDATE_K = int(rag.get("rerank_candidate_k", 15))
        self.RAG_CITATION_ENABLED = _coerce_bool(rag.get("citation_enabled", False))
        self.RAG_FAITHFULNESS_CHECK_ENABLED = _coerce_bool(
            rag.get("faithfulness_check_enabled", False)
        )
        self.RAG_FAITHFULNESS_LLM_ENABLED = _coerce_bool(
            rag.get("faithfulness_llm_enabled", False)
        )
        self.RAG_FAITHFULNESS_THRESHOLD = float(rag.get("faithfulness_threshold", 0.7))
        self.RAG_RERANK_BACKEND = str(rag.get("rerank_backend", "lexical")).lower()
        self.RAG_RERANK_MODEL = str(rag.get("rerank_model", "cross-encoder/ms-marco-MiniLM-L-6-v2"))
        self.RAG_COHERE_API_KEY = secrets.get("COHERE_API_KEY") or str(rag.get("cohere_api_key", "")).strip()
        self.RAG_COHERE_RERANK_MODEL = str(rag.get("cohere_rerank_model", "rerank-english-v3.0"))
        self.RAG_EVAL_RECALL_AT_K = int(rag.get("eval_recall_at_k", 5))
        self.RAG_EVAL_MIN_RECALL = float(rag.get("eval_min_recall", 0.8))
        self.RAG_EVAL_MIN_MRR = float(rag.get("eval_min_mrr", 0.5))
        self.RAG_EVAL_MIN_FAITHFULNESS = float(rag.get("eval_min_faithfulness", 0.75))

        embedding = raw.get("embedding", {})
        if not isinstance(embedding, dict):
            embedding = {}
        self.EMBEDDING_DIMENSION = int(embedding.get("dimension", 0)) or 0
        self.EMBEDDING_DISTANCE_METRIC = str(embedding.get("distance_metric", "cosine"))
        self.EMBEDDING_VERSION = str(embedding.get("version", "v1"))
        self.EMBEDDING_AUTO_REINDEX = _coerce_bool(
            embedding.get("auto_reindex_on_incompatibility", False)
        )

        skill_cfg = raw.get("skill", {})
        if not isinstance(skill_cfg, dict):
            skill_cfg = {}
        self.SKILL_ENABLED = _coerce_bool(skill_cfg.get("enabled", False))
        self.SKILL_CONFIG_DIR = str(
            skill_cfg.get("config_dir", str(Path(__file__).resolve().parents[2] / "config" / "skills"))
        )

        auth = raw.get("auth", {})
        self.AUTH_ENABLED = _coerce_bool(auth.get("enabled", False))
        self.JWT_EXPIRE_MINUTES = int(auth.get("jwt_expire_minutes", 480))
        self.AUTH_USERS = self._parse_users(auth.get("users", []))
        self.AUTH_API_KEYS = self._parse_api_keys(auth.get("api_keys", []))
        self.AUTH_REQUIRE_IN_PRODUCTION = _coerce_bool(
            auth.get("require_in_production", True)
        )

        policy = raw.get("policy", {})
        self.AUTO_REJECT_RISK_LEVEL = str(policy.get("auto_reject_risk_level", "CRITICAL"))
        self.REQUIRE_REVIEW_RISK_LEVEL = str(policy.get("require_review_risk_level", "HIGH"))
        self.MAX_RETRY_COUNT = int(policy.get("max_retry_count", 3))
        self.REVIEW_TIMEOUT_MINUTES = int(policy.get("review_timeout_minutes", 60))
        self.REVIEW_TIMEOUT_ACTION = str(policy.get("review_timeout_action", "reject"))
        self.REVIEW_TIMEOUT_ENABLED = _coerce_bool(policy.get("review_timeout_enabled", True))
        self.REVIEW_TIMEOUT_POLL_SECONDS = int(policy.get("review_timeout_poll_seconds", 30))

        observability = raw.get("observability", {})
        self.LANGSMITH_ENABLED = _coerce_bool(observability.get("langsmith_enabled", False))
        self.LANGSMITH_API_KEY = str(observability.get("langsmith_api_key", ""))
        self.LANGSMITH_PROJECT = str(observability.get("langsmith_project", "agent-langraph"))
        self.METRICS_ENABLED = _coerce_bool(observability.get("metrics_enabled", True))

        batch = raw.get("batch", {})
        self.BATCH_MAX_WORKERS = int(batch.get("max_workers", 4))

        supervisor = raw.get("supervisor", {})
        self.SUPERVISOR_MAX_WORKERS = int(supervisor.get("max_workers", 4))
        self.SUPERVISOR_WORKER_RETRIES = int(supervisor.get("worker_retries", 2))

        queue = raw.get("queue", {})
        self.QUEUE_BACKEND = str(queue.get("backend", "memory"))
        self.REDIS_URL = str(queue.get("redis_url", "redis://localhost:6379/0"))

        scheduler = raw.get("scheduler", {})
        self.SCHEDULER_ENABLED = _coerce_bool(scheduler.get("enabled", True))

        artifacts = raw.get("artifacts", {})
        default_artifacts = str(Path(self.SQLITE_PATH).parent / "artifacts")
        self.ARTIFACTS_PATH = os.environ.get("ARTIFACTS_PATH") or str(
            artifacts.get("base_path", default_artifacts)
        )
        self.ARTIFACT_MAX_WRITE_BYTES = int(artifacts.get("max_write_bytes", 512 * 1024))
        self.ARTIFACT_MAX_FILE_BYTES = int(artifacts.get("max_file_bytes", 5 * 1024 * 1024))
        self.ARTIFACT_CHUNK_CHARS = int(artifacts.get("chunk_chars", 3500))
        self.ARTIFACT_MAX_CHUNKS_PER_TURN = int(artifacts.get("max_chunks_per_turn", 4))
        self.ARTIFACT_MAX_CHARS_PER_TURN = int(artifacts.get("max_chars_per_turn", 14000))

        manuscript = raw.get("manuscript", {})
        self.MANUSCRIPT_DEFAULT_BODY = str(manuscript.get("default_body", "novel.txt"))
        self.MANUSCRIPT_DEFAULT_OUTLINE = str(manuscript.get("default_outline", "outline.txt"))
        self.MANUSCRIPT_MIN_BODY_CHARS = int(manuscript.get("min_body_chars", 200))
        self.MANUSCRIPT_MIN_OUTLINE_CHARS = int(manuscript.get("min_outline_chars", 80))
        self.MANUSCRIPT_TAIL_EXCERPT_CHARS = int(manuscript.get("tail_excerpt_chars", 2400))
        self.MANUSCRIPT_HEAD_EXCERPT_CHARS = int(manuscript.get("head_excerpt_chars", 1200))
        self.MANUSCRIPT_APPEND_DEDUP_RATIO = float(
            manuscript.get("append_dedup_ratio", 0.82)
        )
        self.WRITING_QUALITY_GATE_THRESHOLD = float(
            manuscript.get("quality_gate_threshold", 0.65)
        )
        self.WRITING_L2_TOKEN_BUDGET = int(manuscript.get("l2_token_budget", 1500))
        self.WRITING_L3_TOKEN_BUDGET = int(manuscript.get("l3_token_budget", 1200))
        self.WRITING_ALIGNMENT_RECENT_WINDOW = int(
            manuscript.get("alignment_recent_window", 2)
        )
        self.WRITING_ALIGNMENT_PATCH_MAX_CHAPTERS = int(
            manuscript.get("alignment_patch_max_chapters", 3)
        )
        self.WRITING_BRIDGE_DEFAULT_CHARS = int(
            manuscript.get("bridge_default_chars", 600)
        )
        self.WRITING_RECONCILE_MAX_PATCHES = int(
            manuscript.get("reconcile_max_patches", 3)
        )
        self.WRITING_RECONCILE_MIN_PATCH_CHARS = int(
            manuscript.get("reconcile_min_patch_chars", 80)
        )
        patterns = manuscript.get("placeholder_patterns")
        self.MANUSCRIPT_PLACEHOLDER_PATTERNS = (
            tuple(patterns) if isinstance(patterns, list) else None
        )

        memory_cfg = raw.get("memory", {})
        self.MEMORY_TASK_BOOST = float(memory_cfg.get("task_boost", 0.35))
        self.MEMORY_SESSION_BOOST = float(memory_cfg.get("session_boost", 0.25))
        writeback_raw = memory_cfg.get("writeback", {})
        self.MEMORY_WRITEBACK_CONFIG = (
            writeback_raw if isinstance(writeback_raw, dict) else {}
        )

        display_cfg = raw.get("display", {})
        self.DISPLAY_CONFIG = display_cfg if isinstance(display_cfg, dict) else {}

        delivery_cfg = raw.get("delivery", {})
        self.DELIVERY_CONFIG = delivery_cfg if isinstance(delivery_cfg, dict) else {}

        code_artifact_cfg = raw.get("code_artifact", {})
        self.CODE_ARTIFACT_CONFIG = (
            code_artifact_cfg if isinstance(code_artifact_cfg, dict) else {}
        )

        mission_cfg = raw.get("mission", {})
        if not isinstance(mission_cfg, dict):
            mission_cfg = {}
        self.MISSION_AUTO_FROM_PLANNING = _coerce_bool(
            mission_cfg.get(
                "auto_from_planning",
                performance.get("mission_auto_from_planning", True),
            )
        )
        self.MISSION_AUTO_MIN_TOTAL_CHARS = int(
            mission_cfg.get("auto_min_total_chars", 50000)
        )
        self.MISSION_CHARS_PER_STEP = int(mission_cfg.get("chars_per_step", 4000))
        self.MISSION_STEPS_HARD_CAP = int(mission_cfg.get("steps_hard_cap", 500))
        self.MISSION_MAX_STEPS = int(mission_cfg.get("max_steps", 500))
        self.MISSION_MAX_WALL_SEC = int(mission_cfg.get("max_wall_sec", 3600))
        self.MISSION_MAX_FAILURES = int(mission_cfg.get("max_failures", 3))
        self.MISSION_WRITING_MAX_STEPS = int(mission_cfg.get("writing_max_steps", 500))
        self.MISSION_OUTLINE_MAX_CHARS = int(mission_cfg.get("outline_max_chars", 12000))
        self.MISSION_LLM_DECIDE = _coerce_bool(mission_cfg.get("llm_decide", False))
        self.MISSION_WRITING_LLM_DECIDE = _coerce_bool(
            mission_cfg.get("writing_llm_decide", True)
        )
        self.MISSION_WRITING_REVIEW_EVERY_CHAPTERS = int(
            mission_cfg.get("writing_review_every_chapters", 0)
        )
        self.MISSION_ORCHESTRATION_MIN_CHARS = int(
            mission_cfg.get("orchestration_min_chars", 8000)
        )

        route_audit_cfg = raw.get("route_audit", {})
        self.ROUTE_AUDIT_CONFIG = (
            route_audit_cfg if isinstance(route_audit_cfg, dict) else {}
        )
        self.ROUTE_AUDIT_ENABLED = _coerce_bool(
            route_audit_cfg.get("enabled", True)
            if isinstance(route_audit_cfg, dict)
            else True
        )

        confirmation_gates_cfg = raw.get("confirmation_gates", {})
        self.CONFIRMATION_GATES_CONFIG = (
            confirmation_gates_cfg if isinstance(confirmation_gates_cfg, dict) else {}
        )

        reflection_cfg = raw.get("reflection", {})
        self.REFLECTION_ENABLED = _coerce_bool(reflection_cfg.get("enabled", True))
        self.REFLECTION_MAX_ROUNDS = int(reflection_cfg.get("max_rounds", 2))
        self.REFLECTION_WRITING_ONLY = _coerce_bool(reflection_cfg.get("writing_only", True))
        self.REFLECTION_ROUTE_AUDIT_ON_MISROUTE = _coerce_bool(
            reflection_cfg.get("route_audit_on_misroute", True)
        )

        reasoning_cfg = raw.get("reasoning", {})
        self.REASONING_MODE = str(reasoning_cfg.get("mode", "direct")).lower()

        react_cfg = raw.get("react_loop", {})
        if not isinstance(react_cfg, dict):
            react_cfg = {}
        self.REACT_LOOP_ENABLED = _coerce_bool(react_cfg.get("enabled", False))
        self.REACT_LOOP_MAX_STEPS = int(react_cfg.get("max_steps", 4))
        self.REACT_LOOP_MAX_STEPS_COMPLEX = int(react_cfg.get("max_steps_complex", 6))
        self.REACT_LOOP_MAX_REPLAN = int(react_cfg.get("max_replan", 2))
        self.REACT_LOOP_MAX_FAILURES = int(react_cfg.get("max_failures", 2))
        self.REACT_LOOP_REPLAN_ENABLED = _coerce_bool(react_cfg.get("replan_enabled", True))
        self.REACT_LOOP_LLM_DECIDE = _coerce_bool(react_cfg.get("llm_decide", False))
        allowed_raw = react_cfg.get("allowed_actions")
        self.REACT_LOOP_ALLOWED_ACTIONS = (
            [str(a) for a in allowed_raw] if isinstance(allowed_raw, list) else []
        )
        self.REACT_ROUTE_RECOMMEND_MIN_CONFIDENCE = float(
            react_cfg.get("route_recommend_min_confidence", 0.75)
        )

        resource_cfg = raw.get("resource", {})
        self.DEFAULT_TOKEN_BUDGET = int(resource_cfg.get("default_token_budget", 0))
        self.DEFAULT_COST_BUDGET = float(resource_cfg.get("default_cost_budget", 0))
        self.COST_PER_1K_TOKENS = float(resource_cfg.get("cost_per_1k_tokens", 0))
        self.BUDGET_DOWNGRADE_RATIO = float(resource_cfg.get("downgrade_ratio", 0.8))
        self.BUDGET_DOWNGRADE_MAX_TOKENS = int(resource_cfg.get("downgrade_max_tokens", 2048))
        self.MODEL_FALLBACK_NAME = str(resource_cfg.get("fallback_model", "")).strip()

        output_guard_cfg = raw.get("output_guard", {})
        self.OUTPUT_GUARD_ENABLED = _coerce_bool(output_guard_cfg.get("enabled", True))
        self.OUTPUT_GUARD_LLM_REVIEW = _coerce_bool(output_guard_cfg.get("llm_review", False))
        self.OUTPUT_GUARD_PII_PROSE_ONLY = _coerce_bool(
            output_guard_cfg.get("pii_prose_only", True)
        )

        queue_cfg = raw.get("task_queue", raw.get("queue", {}))
        self.QUEUE_POLL_SECONDS = int(queue_cfg.get("poll_seconds", 10))
        self.QUEUE_MAX_PER_TICK = int(queue_cfg.get("max_per_tick", 4))
        self.QUEUE_STARVATION_SEC = int(queue_cfg.get("starvation_sec", 300))
        self.QUEUE_STARVATION_MAX_BOOST = int(queue_cfg.get("starvation_max_boost", 3))

        llm_purpose = raw.get("llm_purpose", {})
        self.LLM_PURPOSE_CONFIG = llm_purpose if isinstance(llm_purpose, dict) else {}

        agent_cfg_tools = raw.get("agent", {})
        self.TOOL_EXEC_MAX_WORKERS = int(
            agent_cfg_tools.get("tool_exec_max_workers", performance.get("tool_exec_max_workers", 4))
        )

        tools = raw.get("tools", {})
        self.HTTP_TOOLS = tools.get("http", []) if isinstance(tools.get("http", []), list) else []
        self.HTTP_TOOL_TIMEOUT = int(tools.get("http_timeout", 30))

        mcp = raw.get("mcp", {})
        self.MCP_ENABLED = _coerce_bool(mcp.get("enabled", False))
        self.MCP_TIMEOUT = int(mcp.get("timeout", 30))
        self.MCP_SERVERS = mcp.get("servers", []) if isinstance(mcp.get("servers", []), list) else []
        self.MCP_MAX_FAILURES_BEFORE_EVICT = int(mcp.get("max_failures_before_evict", 3))
        self.MCP_AUTO_EVICT = _coerce_bool(mcp.get("auto_evict", True))
        self.MCP_REGISTER_RESOURCES = _coerce_bool(mcp.get("register_resources", True))
        self.MCP_REGISTER_PROMPTS = _coerce_bool(mcp.get("register_prompts", False))

        rate_limit = raw.get("rate_limit", {})
        self.RATE_LIMIT_ENABLED = _coerce_bool(rate_limit.get("enabled", True))
        self.RATE_LIMIT_USER_PER_MIN = int(rate_limit.get("user_per_minute", 10))
        self.RATE_LIMIT_IP_PER_MIN = int(rate_limit.get("ip_per_minute", 100))
        self.RATE_LIMIT_WINDOW_SEC = int(rate_limit.get("window_sec", 60))
        self.RATE_LIMIT_REDIS = _coerce_bool(rate_limit.get("use_redis", False))

        checkpoint_cfg = raw.get("checkpoint", {})
        self.CHECKPOINT_RETENTION_DAYS = int(checkpoint_cfg.get("retention_days", 30))
        self.CHECKPOINT_CLEANUP_ENABLED = _coerce_bool(checkpoint_cfg.get("cleanup_enabled", True))

        memory_compress_cfg = raw.get("memory_compress", {})
        self.MEMORY_COMPRESS_ENABLED = _coerce_bool(memory_compress_cfg.get("enabled", True))
        self.MEMORY_COMPRESS_MIN_CHARS = int(memory_compress_cfg.get("min_chars", 2000))

        a2a_cfg = raw.get("a2a", {})
        self.A2A_HTTP_FORWARD_ENABLED = _coerce_bool(a2a_cfg.get("http_forward_enabled", True))
        self.A2A_REGISTRY_TTL_SEC = int(a2a_cfg.get("registry_ttl_sec", 300))
        self.A2A_SELF_URL = str(a2a_cfg.get("self_url", "")).strip()

        mission_reflect = raw.get("mission_micro_reflect", {})
        self.MISSION_MICRO_REFLECT_ENABLED = _coerce_bool(mission_reflect.get("enabled", True))
        self.MISSION_MICRO_REFLECT_THRESHOLD = float(mission_reflect.get("confidence_threshold", 0.5))

        explore_cfg = raw.get("exploration", {})
        self.EXPLORATION_LLM_SCORE_ENABLED = _coerce_bool(explore_cfg.get("llm_score_enabled", True))

        tenant_cfg = raw.get("tenant", {})
        if not isinstance(tenant_cfg, dict):
            tenant_cfg = {}
        self.MULTI_TENANT_ENABLED = _coerce_bool(tenant_cfg.get("enabled", False))
        self.TENANT_MAX_TASKS_PER_DAY = int(tenant_cfg.get("max_tasks_per_day", 0))
        self.TENANT_MAX_TOKENS_PER_DAY = int(tenant_cfg.get("max_tokens_per_day", 0))
        self.TENANT_MAX_CONCURRENT_TASKS = int(tenant_cfg.get("max_concurrent_tasks", 0))
        quota_backend = str(tenant_cfg.get("quota_backend", "memory")).lower()
        self.TENANT_QUOTA_BACKEND = quota_backend
        self.TENANT_QUOTA_REDIS = _coerce_bool(
            tenant_cfg.get("quota_use_redis", quota_backend == "redis")
        )

        graph_runner_cfg = raw.get("graph_runner", {})
        self.GRAPH_RUNNER_BACKPRESSURE_ENABLED = _coerce_bool(
            graph_runner_cfg.get("backpressure_enabled", True)
        )
        self.GRAPH_RUNNER_MAX_CONCURRENT = int(graph_runner_cfg.get("max_concurrent", 8))
        self.GRAPH_RUNNER_QUEUE_TIMEOUT_SEC = float(
            graph_runner_cfg.get("queue_timeout_sec", 30)
        )

        llm_cb = raw.get("llm", {})
        if not isinstance(llm_cb, dict):
            llm_cb = {}
        self.LLM_CIRCUIT_BREAKER_ENABLED = _coerce_bool(
            llm_cb.get("circuit_breaker_enabled", False)
        )
        self.LLM_CIRCUIT_FAILURE_THRESHOLD = int(llm_cb.get("failure_threshold", 5))
        self.LLM_CIRCUIT_RECOVERY_TIMEOUT_SEC = float(
            llm_cb.get("recovery_timeout_seconds", 60)
        )

        ctx_compress = raw.get("context_compress", {})
        if not isinstance(ctx_compress, dict):
            ctx_compress = {}
        self.CONTEXT_COMPRESS_SEMANTIC_ENABLED = _coerce_bool(
            ctx_compress.get("semantic_enabled", False)
        )
        self.CONTEXT_COMPRESS_MIN_CHARS_FOR_SEMANTIC = int(
            ctx_compress.get("min_chars_for_semantic", 4000)
        )
        self.CONTEXT_COMPRESS_KEEP_RECENT_TURNS = int(
            ctx_compress.get("keep_recent_turns", 6)
        )
        self.CONTEXT_COMPRESS_SUMMARY_MAX_CHARS = int(
            ctx_compress.get("summary_max_chars", 1200)
        )
        self.CONTEXT_COMPRESS_MIN_RATIO = float(
            ctx_compress.get("min_ratio", 0.5)
        )

        self.CHECKPOINT_CORRUPTION_DETECTION_ENABLED = _coerce_bool(
            checkpoint_cfg.get("corruption_detection_enabled", True)
        )
        self.CHECKPOINT_AUTO_RESET_ON_CORRUPT = _coerce_bool(
            checkpoint_cfg.get("auto_reset_on_corrupt", False)
        )
        self.CHECKPOINT_MAX_NODE_REPEAT = int(checkpoint_cfg.get("max_node_repeat", 10))

    def _parse_users(self, users: Any) -> list[dict[str, str]]:
        if not isinstance(users, list):
            return []
        parsed: list[dict[str, str]] = []
        for item in users:
            if not isinstance(item, dict) or not item.get("username"):
                continue
            if not item.get("password") and not item.get("password_hash"):
                continue
            entry: dict[str, str] = {
                "username": str(item["username"]),
                "user_id": str(item.get("user_id", item["username"])),
                "role": str(item.get("role", "user")),
            }
            if item.get("password_hash"):
                entry["password_hash"] = str(item["password_hash"])
            if item.get("password"):
                entry["password"] = str(item["password"])
            if item.get("tenant_id"):
                entry["tenant_id"] = str(item["tenant_id"])
            if item.get("tenant_ids"):
                entry["tenant_ids"] = item["tenant_ids"]
            parsed.append(entry)
        return parsed

    def _parse_api_keys(self, keys: Any) -> list[dict[str, str]]:
        if not isinstance(keys, list):
            return []
        parsed: list[dict[str, str]] = []
        for item in keys:
            if isinstance(item, dict) and item.get("key"):
                entry = {
                    "key": str(item["key"]),
                    "user_id": str(item.get("user_id", "service")),
                    "role": str(item.get("role", "user")),
                }
                if item.get("tenant_id"):
                    entry["tenant_id"] = str(item["tenant_id"])
                if item.get("tenant_ids"):
                    entry["tenant_ids"] = item["tenant_ids"]
                parsed.append(entry)
        return parsed

    def _load_yaml(self, config_path: str) -> dict[str, Any]:
        with open(config_path, encoding="utf-8") as handle:
            content = _resolve_env(handle.read())
        loaded = yaml.safe_load(content)
        return loaded if isinstance(loaded, dict) else {}


settings = Settings()
