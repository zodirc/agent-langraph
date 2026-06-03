from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.services.audit_store import AuditStore
from app.services.knowledge_store import KnowledgeStore
from app.services.memory_store import MemoryStore
from app.services.state_store import StateStore
from app.services.tool_registry import ToolRegistry


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    os.environ["ANTHROPIC_API_KEY"] = ""
    return db_dir


@pytest.fixture
def test_settings(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    db_path = str(tmp_data_dir / "agent.db")
    config_content = f"""
model:
  provider: anthropic
  name: claude-sonnet-4-5
  base_url: https://api.anthropic.com
  api_key: ""
  enabled: false
  temperature: 0
  max_tokens: 1024
  timeout: 30
  max_retries: 1
storage:
  sqlite_path: {db_path}
  checkpoint_sqlite_path: {tmp_data_dir / "checkpoints.db"}
  vectorstore_path: {tmp_data_dir / "vectorstore"}
  log_path: {tmp_data_dir / "agent.log"}
app:
  env: test
  port: 8000
  debug: true
  secret_key: test-secret
knowledge:
  backend: chroma
  top_k: 3
  similarity_threshold: 0.1
auth:
  enabled: false
batch:
  max_workers: 2
scheduler:
  enabled: false
mcp:
  enabled: false
supervisor:
  max_workers: 2
  worker_retries: 1
policy:
  auto_reject_risk_level: CRITICAL
  require_review_risk_level: HIGH
  max_retry_count: 3
  review_timeout_enabled: false
artifacts:
  base_path: {tmp_data_dir / "artifacts"}
performance:
  fast_reasoning_enabled: true
  skip_retrieval_when_no_tools: true
session:
  enabled: true
  max_history_turns: 20
  max_history_chars: 8000
  compress_enabled: true
observability:
  metrics_enabled: false
rate_limit:
  enabled: false
checkpoint:
  cleanup_enabled: false
memory_compress:
  enabled: false
mission_micro_reflect:
  enabled: false
exploration:
  llm_score_enabled: false
graph_runner:
  backpressure_enabled: true
  max_concurrent: 32
  queue_timeout_sec: 60
llm:
  circuit_breaker_enabled: false
context_compress:
  semantic_enabled: false
react_loop:
  enabled: false
  max_steps: 4
  replan_enabled: true
  llm_decide: false
skill:
  enabled: true
  runtime_policy_enabled: true
  config_dir: config/skills
"""
    config_path = tmp_data_dir / "config.yaml"
    config_path.write_text(config_content, encoding="utf-8")
    settings = Settings(str(config_path))
    import app.config.settings as settings_module
    import app.nodes.tool_node as tool_node_module  # noqa: F401 — used in loop below
    import app.runtime.graph as graph_module
    import app.runtime.supervisor_graph as supervisor_graph_module
    import app.services.review_timeout_service as review_timeout_module
    import app.services.audit_store as audit_store_module
    import app.services.knowledge_store as knowledge_store_module
    import app.services.fast_reasoning as fast_reasoning_module
    import app.services.llm_client as llm_client_module
    import app.services.memory_store as memory_store_module
    import app.services.policy_engine as policy_engine_module
    import app.services.state_store as state_store_module
    import app.services.reasoning_trace as reasoning_trace_module

    monkeypatch.setattr(settings_module, "settings", settings)
    monkeypatch.setattr(reasoning_trace_module, "settings", settings)
    monkeypatch.setattr(state_store_module, "settings", settings)
    monkeypatch.setattr(memory_store_module, "settings", settings)
    monkeypatch.setattr(audit_store_module, "settings", settings)
    monkeypatch.setattr(knowledge_store_module, "settings", settings)
    monkeypatch.setattr(llm_client_module, "settings", settings)
    monkeypatch.setattr(fast_reasoning_module, "settings", settings)
    llm_client_module.get_llm.cache_clear()
    monkeypatch.setattr(policy_engine_module, "settings", settings)
    import app.services.db as db_module

    monkeypatch.setattr(db_module, "settings", settings)
    monkeypatch.setattr(review_timeout_module, "settings", settings)
    import app.services.graph_execution_pool as graph_pool_module
    import app.services.context_compressor as context_compress_module
    import app.services.checkpoint_recovery as checkpoint_recovery_module
    import app.services.circuit_breaker as circuit_breaker_module
    import app.services.session_turn as session_turn_module
    import app.services.metrics_service as metrics_service_module
    import app.services.reranker as reranker_module
    import app.services.rag_eval as rag_eval_module
    import app.services.embedding_meta as embedding_meta_module
    import app.services.skill_registry as skill_registry_module
    import app.services.mcp_manager as mcp_manager_module
    import app.services.mcp_bridge as mcp_bridge_module
    import app.services.react_entry as react_entry_module
    import app.services.react_loop_runner as react_loop_runner_module
    import app.services.runtime_router as runtime_router_module

    for mod in (
        graph_pool_module,
        context_compress_module,
        checkpoint_recovery_module,
        circuit_breaker_module,
        session_turn_module,
        metrics_service_module,
        reranker_module,
        rag_eval_module,
        embedding_meta_module,
        skill_registry_module,
        mcp_manager_module,
        mcp_bridge_module,
        react_entry_module,
        react_loop_runner_module,
        runtime_router_module,
    ):
        monkeypatch.setattr(mod, "settings", settings)
    return settings


def _reset_service_singletons() -> None:
    import app.runtime.graph as graph_module
    import app.runtime.mission_graph as mission_graph_module
    import app.runtime.supervisor_graph as supervisor_graph_module
    import app.runtime.checkpointer as checkpointer_module
    import app.runtime.worker_graph as worker_graph_module
    import app.services.audit_store as audit_mod
    import app.services.graph_runner as runner_mod
    import app.services.knowledge_store as knowledge_mod
    import app.services.memory_store as memory_mod
    import app.services.state_store as state_mod
    import app.services.batch_store as batch_mod
    import app.services.schedule_store as schedule_mod
    import app.services.task_queue_store as tq_mod
    import app.services.pack_params_store as pack_mod
    import app.runtime.exploration_graph as explore_mod
    import app.services.dead_letter_store as dlq_mod
    import app.services.tool_bootstrap as bootstrap_mod
    import app.services.tool_registry as tool_mod
    import app.services.metrics_service as metrics_mod
    import app.services.graph_execution_pool as pool_mod
    import app.services.circuit_breaker as cb_mod
    import app.services.skill_registry as skill_mod

    pool_mod.reset_graph_execution_pool()
    cb_mod.reset_circuit_breakers()
    skill_mod.reset_skill_registry()
    import app.services.skill_store as skill_store_mod

    skill_store_mod.reset_skill_store()
    import app.services.mcp_manager as mcp_mgr_mod

    mcp_mgr_mod.reset_mcp_manager()
    state_mod._store = None
    dlq_mod._store = None
    metrics_mod._service = None
    memory_mod._store = None
    audit_mod._store = None
    knowledge_mod._store = None
    tool_mod._registry = None
    runner_mod._runner = None
    batch_mod._store = None
    schedule_mod._store = None
    tq_mod._store = None
    pack_mod._store = None
    bootstrap_mod.reset_tool_bootstrap()
    graph_module.get_compiled_graph.cache_clear()
    mission_graph_module.get_compiled_mission_graph.cache_clear()
    supervisor_graph_module.get_compiled_supervisor_graph.cache_clear()
    explore_mod.get_compiled_exploration_graph.cache_clear()
    import app.runtime.worker_graph as worker_graph_module

    worker_graph_module.get_compiled_worker_graph.cache_clear()
    checkpointer_module.reset_checkpointer()


@pytest.fixture
def isolated_stores(test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_service_singletons()
    state_store = StateStore(test_settings.SQLITE_PATH)
    memory_store = MemoryStore(test_settings.SQLITE_PATH)
    audit_store = AuditStore(test_settings.SQLITE_PATH)
    knowledge_store = KnowledgeStore(test_settings.SQLITE_PATH)
    registry = ToolRegistry()

    monkeypatch.setattr("app.services.state_store.get_state_store", lambda: state_store)
    monkeypatch.setattr("app.services.memory_store.get_memory_store", lambda: memory_store)
    monkeypatch.setattr("app.services.audit_store.get_audit_store", lambda: audit_store)
    monkeypatch.setattr("app.services.knowledge_store.get_knowledge_store", lambda: knowledge_store)
    dlq_store = __import__(
        "app.services.dead_letter_store", fromlist=["DeadLetterStore"]
    ).DeadLetterStore(test_settings.SQLITE_PATH)
    monkeypatch.setattr("app.services.dead_letter_store.get_dead_letter_store", lambda: dlq_store)
    monkeypatch.setattr("app.services.tool_registry.get_tool_registry", lambda: registry)
    import app.nodes.human_review_node as human_review_node_module
    import app.nodes.memory_writeback_node as memory_writeback_node_module
    import app.nodes.output_node as output_node_module
    import app.nodes.planning_node as planning_node_module
    import app.nodes.policy_node as policy_node_module
    import app.nodes.reasoning_node as reasoning_node_module
    import app.nodes.retrieval_node as retrieval_node_module
    import app.nodes.tool_node as tool_node_module
    import app.nodes.writing_node as writing_node_module
    import app.nodes.mission_init_node as mission_init_module
    import app.nodes.mission_decide_node as mission_decide_module
    import app.nodes.mission_act_node as mission_act_module
    import app.nodes.mission_observe_node as mission_observe_module
    import app.nodes.mission_eval_node as mission_eval_module
    import app.nodes.mission_finalize_node as mission_finalize_module
    import app.nodes.react_deliberate_node as react_deliberate_module
    import app.nodes.react_execute_node as react_execute_module
    import app.nodes.react_observe_node as react_observe_module
    import app.nodes.react_finalize_node as react_finalize_module
    import app.services.graph_runner as graph_runner_module

    for module in (
        planning_node_module,
        retrieval_node_module,
        tool_node_module,
        writing_node_module,
        reasoning_node_module,
        policy_node_module,
        human_review_node_module,
        output_node_module,
        memory_writeback_node_module,
        mission_init_module,
        mission_decide_module,
        mission_act_module,
        mission_observe_module,
        mission_eval_module,
        mission_finalize_module,
        react_deliberate_module,
        react_execute_module,
        react_observe_module,
        react_finalize_module,
    ):
        monkeypatch.setattr(module, "get_state_store", lambda: state_store)
    monkeypatch.setattr(output_node_module, "get_audit_store", lambda: audit_store)
    monkeypatch.setattr(graph_runner_module, "get_state_store", lambda: state_store)
    monkeypatch.setattr(graph_runner_module, "get_audit_store", lambda: audit_store)
    monkeypatch.setattr("app.services.graph_runner.get_state_store", lambda: state_store)
    monkeypatch.setattr("app.services.graph_runner.get_audit_store", lambda: audit_store)

    knowledge_store.upsert_document(
        "Agent Architecture",
        "LangGraph runtime with planning retrieval reasoning policy nodes.",
        metadata={"source": "test"},
    )

    from app.runtime import graph as graph_module
    from app.runtime import mission_graph as mission_graph_module
    from app.runtime import supervisor_graph as supervisor_graph_module
    from app.runtime import worker_graph as worker_graph_module

    graph_module.get_compiled_graph.cache_clear()
    mission_graph_module.get_compiled_mission_graph.cache_clear()
    supervisor_graph_module.get_compiled_supervisor_graph.cache_clear()
    worker_graph_module.get_compiled_worker_graph.cache_clear()
    return state_store


@pytest.fixture
def base_state(isolated_stores) -> dict:
    from app.runtime.state import create_initial_state

    return create_initial_state(
        task_id="test-task-001",
        user_id="tester",
        task_type="qa",
        input_payload={"goal": "Explain LangGraph agent runtime", "risk_level": "LOW"},
    )


def pytest_sessionstart(session: pytest.Session) -> None:
    try:
        import pysqlite3  # type: ignore

        sys.modules.setdefault("sqlite3", pysqlite3)
    except ImportError:
        return
