from __future__ import annotations

import os
import threading
from typing import Any

from app.config.settings import settings


class MetricsService:
    """Lightweight metrics (Prometheus when enabled, in-memory otherwise) §14.2."""

    def __init__(self, *, registry: Any | None = None) -> None:
        self._lock = threading.Lock()
        self._registry: Any | None = registry
        self._tenant_cost_usd: dict[str, float] = {}
        self._context_compress_ratios: list[tuple[str, float]] = []
        self._counters: dict[str, float] = {
            "tasks_created": 0,
            "tasks_completed": 0,
            "tasks_rejected": 0,
            "tasks_dead_letter": 0,
            "reviews_waiting": 0,
            "reviews_resolved": 0,
            "policy_reviews": 0,
            "policy_rejects": 0,
            "tool_failures": 0,
            "node_executions": 0,
            "tasks_with_retry": 0,
            "tasks_failed": 0,
            "reasoning_parser_repaired": 0,
            "reasoning_parser_fallback": 0,
            "session_memory_queries": 0,
            "session_memory_hits": 0,
            "contract_events": 0,
            "react_loop_entered": 0,
            "react_loop_finished": 0,
            "react_loop_aborted": 0,
        }
        self._prometheus = None
        self._histograms: dict[str, Any] = {}
        if settings.METRICS_ENABLED:
            try:
                from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

                # Under pytest, MetricsService may be re-instantiated multiple times.
                # Use an isolated registry to prevent "Duplicated timeseries" errors.
                if self._registry is None and os.environ.get("PYTEST_CURRENT_TEST"):
                    self._registry = CollectorRegistry()

                # Tests may instantiate MetricsService multiple times; use a dedicated registry
                # to avoid "Duplicated timeseries" errors unless the caller wants the default.
                prom_kwargs: dict[str, Any] = {}
                if self._registry is not None:
                    prom_kwargs["registry"] = self._registry

                self._prometheus = {
                    "tasks_created": Counter(
                        "agent_tasks_created_total", "Tasks created", **prom_kwargs
                    ),
                    "tasks_completed": Counter(
                        "agent_tasks_completed_total", "Tasks completed", **prom_kwargs
                    ),
                    "tasks_dead_letter": Counter(
                        "agent_tasks_dead_letter_total", "Tasks sent to DLQ", **prom_kwargs
                    ),
                    "node_executions": Counter(
                        "agent_node_executions_total",
                        "Node executions",
                        ["node"],
                        **prom_kwargs,
                    ),
                    "llm_invoke": Counter(
                        "agent_llm_invoke_total",
                        "LLM invocations",
                        ["purpose", "model", "status"],
                        **prom_kwargs,
                    ),
                    "llm_tokens": Counter(
                        "agent_llm_tokens_total",
                        "LLM tokens",
                        ["purpose", "kind"],
                        **prom_kwargs,
                    ),
                    "llm_cost_usd": Counter(
                        "agent_llm_cost_usd_total",
                        "Estimated LLM cost in USD",
                        ["tenant_id", "user_id"],
                        **prom_kwargs,
                    ),
                    "tenant_tasks": Counter(
                        "agent_tenant_tasks_total",
                        "Tasks per tenant",
                        ["tenant_id"],
                        **prom_kwargs,
                    ),
                    "tenant_tokens": Counter(
                        "agent_tenant_tokens_total",
                        "LLM tokens per tenant",
                        ["tenant_id"],
                        **prom_kwargs,
                    ),
                    "tenant_quota_exceeded": Counter(
                        "agent_tenant_quota_exceeded_total",
                        "Tenant quota exceeded events",
                        ["tenant_id", "resource"],
                        **prom_kwargs,
                    ),
                    "budget_exhausted": Counter(
                        "agent_budget_exhausted_total",
                        "Budget exhausted events",
                        ["purpose"],
                        **prom_kwargs,
                    ),
                    "tool_invocations": Counter(
                        "agent_tool_invocations_total",
                        "Tool invocations",
                        ["tool", "status"],
                        **prom_kwargs,
                    ),
                    "graph_rejected": Counter(
                        "agent_graph_rejected_total",
                        "Graph executions rejected (backpressure)",
                        **prom_kwargs,
                    ),
                    "checkpoint_corrupt": Counter(
                        "agent_checkpoint_corrupt_total",
                        "Corrupt checkpoints detected",
                        **prom_kwargs,
                    ),
                    "checkpoint_reset": Counter(
                        "agent_checkpoint_reset_total",
                        "Checkpoint thread resets",
                        ["reason"],
                        **prom_kwargs,
                    ),
                    "llm_error": Counter(
                        "agent_llm_error_total",
                        "LLM errors by category",
                        ["category"],
                        **prom_kwargs,
                    ),
                    "reasoning_parser_events": Counter(
                        "agent_reasoning_parser_events_total",
                        "Reasoning parser repair/fallback events",
                        ["kind"],
                        **prom_kwargs,
                    ),
                }
                self._prometheus["graph_queue_active"] = Gauge(
                    "agent_graph_queue_active",
                    "Active graph executions",
                    **prom_kwargs,
                )
                self._prometheus["graph_queue_waiting"] = Gauge(
                    "agent_graph_queue_waiting",
                    "Graph executions waiting for slot",
                    **prom_kwargs,
                )
                self._prometheus["llm_circuit_state"] = Gauge(
                    "agent_llm_circuit_state",
                    "LLM circuit breaker (1=open/half_open active path)",
                    ["name", "state"],
                    **prom_kwargs,
                )
                self._histograms["node_duration"] = Histogram(
                    "agent_node_duration_seconds",
                    "Node execution duration",
                    ["node"],
                    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 15.0, 30.0, 60.0, 120.0),
                    **prom_kwargs,
                )
                self._histograms["context_compress_ratio"] = Histogram(
                    "agent_context_compress_ratio",
                    "Context compression ratio (1 - after/before chars)",
                    ["method"],
                    buckets=(0.1, 0.25, 0.4, 0.5, 0.6, 0.75, 0.9, 1.0),
                    **prom_kwargs,
                )
                self._prometheus["react_loop_outcomes"] = Counter(
                    "agent_react_loop_outcomes_total",
                    "SRDL loop completions",
                    ["status", "exit_path"],
                    **prom_kwargs,
                )
                self._prometheus["react_action_selected"] = Counter(
                    "agent_react_action_total",
                    "SRDL actions selected or executed",
                    ["action"],
                    **prom_kwargs,
                )
                self._prometheus["react_action_blocked"] = Counter(
                    "agent_react_action_blocked_total",
                    "SRDL actions blocked (not in whitelist)",
                    ["action"],
                    **prom_kwargs,
                )
                self._prometheus["react_replan"] = Counter(
                    "agent_react_replan_total",
                    "SRDL replan events",
                    **prom_kwargs,
                )
                self._prometheus["react_runtime_upgrade"] = Counter(
                    "agent_react_runtime_upgrade_total",
                    "SRDL approved runtime upgrade suggestions",
                    ["runtime"],
                    **prom_kwargs,
                )
                self._histograms["react_loop_steps"] = Histogram(
                    "agent_react_loop_steps",
                    "Steps completed in one SRDL loop",
                    buckets=(0, 1, 2, 3, 4, 5, 6, 8),
                    **prom_kwargs,
                )
            except ImportError:
                pass

    def _inc(self, key: str, value: float = 1.0) -> None:
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + value

    def inc_task_created(self) -> None:
        self._inc("tasks_created")
        if self._prometheus:
            self._prometheus["tasks_created"].inc()

    def inc_task_completed(self, *, retry_count: int = 0) -> None:
        self._inc("tasks_completed")
        if retry_count > 0:
            self._inc("tasks_with_retry")
        if self._prometheus:
            self._prometheus["tasks_completed"].inc()

    def inc_task_failed(self) -> None:
        self._inc("tasks_failed")

    def inc_dead_letter(self) -> None:
        self._inc("tasks_dead_letter")
        if self._prometheus:
            self._prometheus["tasks_dead_letter"].inc()

    def inc_node(self, node: str, *, duration_sec: float | None = None) -> None:
        self._inc("node_executions")
        if self._prometheus and "node_executions" in self._prometheus:
            self._prometheus["node_executions"].labels(node=node).inc()
        if duration_sec is not None and "node_duration" in self._histograms:
            self._histograms["node_duration"].labels(node=node).observe(duration_sec)

    def inc_llm_invoke(self, purpose: str, model: str, status: str) -> None:
        if self._prometheus and "llm_invoke" in self._prometheus:
            self._prometheus["llm_invoke"].labels(
                purpose=purpose, model=model, status=status
            ).inc()

    def inc_llm_tokens(self, purpose: str, kind: str, count: int) -> None:
        if count <= 0:
            return
        if self._prometheus and "llm_tokens" in self._prometheus:
            self._prometheus["llm_tokens"].labels(purpose=purpose, kind=kind).inc(count)

    def record_llm_cost_usd(
        self,
        cost_usd: float,
        *,
        tenant_id: str = "default",
        user_id: str = "anonymous",
    ) -> None:
        if cost_usd <= 0:
            return
        tid = (tenant_id or "default")[:40]
        uid = (user_id or "anonymous")[:40]
        with self._lock:
            self._tenant_cost_usd[tid] = self._tenant_cost_usd.get(tid, 0.0) + cost_usd
        if self._prometheus and "llm_cost_usd" in self._prometheus:
            self._prometheus["llm_cost_usd"].labels(tenant_id=tid, user_id=uid).inc(cost_usd)

    def tenant_llm_cost_usd(self, tenant_id: str) -> float:
        tid = (tenant_id or "default")[:40]
        with self._lock:
            return round(self._tenant_cost_usd.get(tid, 0.0), 6)

    def inc_tenant_task(self, tenant_id: str) -> None:
        tid = (tenant_id or "default")[:40]
        if self._prometheus and "tenant_tasks" in self._prometheus:
            self._prometheus["tenant_tasks"].labels(tenant_id=tid).inc()

    def inc_tenant_tokens(self, tenant_id: str, count: int) -> None:
        if count <= 0:
            return
        tid = (tenant_id or "default")[:40]
        if self._prometheus and "tenant_tokens" in self._prometheus:
            self._prometheus["tenant_tokens"].labels(tenant_id=tid).inc(count)

    def inc_tenant_quota_exceeded(self, tenant_id: str, resource: str) -> None:
        tid = (tenant_id or "default")[:40]
        if self._prometheus and "tenant_quota_exceeded" in self._prometheus:
            self._prometheus["tenant_quota_exceeded"].labels(
                tenant_id=tid, resource=resource[:20]
            ).inc()

    def inc_budget_exhausted(self, purpose: str) -> None:
        if self._prometheus and "budget_exhausted" in self._prometheus:
            self._prometheus["budget_exhausted"].labels(purpose=purpose).inc()

    def inc_tool_invocation(self, tool: str, status: str) -> None:
        if self._prometheus and "tool_invocations" in self._prometheus:
            self._prometheus["tool_invocations"].labels(tool=tool, status=status).inc()

    def inc_graph_rejected(self) -> None:
        if self._prometheus and "graph_rejected" in self._prometheus:
            self._prometheus["graph_rejected"].inc()

    def observe_graph_queue(
        self, *, active: int, waiting: int, max_concurrent: int
    ) -> None:
        if not self._prometheus:
            return
        if "graph_queue_active" in self._prometheus:
            self._prometheus["graph_queue_active"].set(active)
        if "graph_queue_waiting" in self._prometheus:
            self._prometheus["graph_queue_waiting"].set(waiting)

    def inc_checkpoint_corrupt(self) -> None:
        if self._prometheus and "checkpoint_corrupt" in self._prometheus:
            self._prometheus["checkpoint_corrupt"].inc()

    def inc_checkpoint_reset(self, reason: str = "unknown") -> None:
        if self._prometheus and "checkpoint_reset" in self._prometheus:
            self._prometheus["checkpoint_reset"].labels(reason=reason[:40]).inc()

    def inc_llm_error(self, category: str) -> None:
        if self._prometheus and "llm_error" in self._prometheus:
            self._prometheus["llm_error"].labels(category=category).inc()

    def inc_session_memory_retrieval(self, *, hit: bool) -> None:
        self._inc("session_memory_queries")
        if hit:
            self._inc("session_memory_hits")

    def inc_reasoning_parser_event(self, kind: str) -> None:
        kind_key = (kind or "unknown").strip().lower()[:24] or "unknown"
        if kind_key == "repaired":
            self._inc("reasoning_parser_repaired")
        elif kind_key == "fallback":
            self._inc("reasoning_parser_fallback")
        else:
            self._inc(f"reasoning_parser_{kind_key}")
        if self._prometheus and "reasoning_parser_events" in self._prometheus:
            self._prometheus["reasoning_parser_events"].labels(kind=kind_key).inc()

    def inc_contract_event(self, kind: str) -> None:
        kind_key = (kind or "unknown").strip().lower()[:40] or "unknown"
        self._inc("contract_events")
        self._inc(f"contract_{kind_key}")

    def set_llm_circuit_state(self, name: str, state: str) -> None:
        if self._prometheus and "llm_circuit_state" in self._prometheus:
            for s in ("closed", "open", "half_open"):
                self._prometheus["llm_circuit_state"].labels(name=name, state=s).set(
                    1.0 if s == state else 0.0
                )

    def observe_context_compress_ratio(self, ratio: float, *, method: str = "semantic") -> None:
        """Record compression ratio for session/context history (v0.12 gate)."""
        ratio = max(0.0, min(1.0, float(ratio)))
        method_key = (method or "unknown")[:20]
        with self._lock:
            self._context_compress_ratios.append((method_key, ratio))
        if "context_compress_ratio" in self._histograms:
            self._histograms["context_compress_ratio"].labels(method=method_key).observe(ratio)

    def context_compress_ratio_samples(self) -> list[tuple[str, float]]:
        with self._lock:
            return list(self._context_compress_ratios)

    def mean_context_compress_ratio(self, *, method: str | None = None) -> float | None:
        with self._lock:
            samples = [
                r
                for m, r in self._context_compress_ratios
                if method is None or m == method
            ]
        if not samples:
            return None
        return sum(samples) / len(samples)

    def observe_rag_rerank_latency(self, seconds: float, *, backend: str = "lexical") -> None:
        if "rag_rerank_latency" not in self._histograms:
            try:
                from prometheus_client import Histogram

                self._histograms["rag_rerank_latency"] = Histogram(
                    "agent_rag_rerank_latency_seconds",
                    "RAG rerank latency",
                    ["backend"],
                    **({"registry": self._registry} if self._registry is not None else {}),
                )
            except ImportError:
                return
        self._histograms["rag_rerank_latency"].labels(backend=backend[:20]).observe(seconds)

    def set_mcp_server_health(self, name: str, healthy: bool) -> None:
        if not self._prometheus:
            return
        if "mcp_server_health" not in self._prometheus:
            try:
                from prometheus_client import Gauge

                self._prometheus["mcp_server_health"] = Gauge(
                    "agent_mcp_server_health",
                    "MCP server health (1=healthy)",
                    ["name"],
                    **({"registry": self._registry} if self._registry is not None else {}),
                )
            except ImportError:
                return
        self._prometheus["mcp_server_health"].labels(name=name[:40]).set(1.0 if healthy else 0.0)

    def inc_mcp_eviction(self, name: str, reason: str) -> None:
        if not self._prometheus:
            return
        if "mcp_eviction" not in self._prometheus:
            try:
                from prometheus_client import Counter

                self._prometheus["mcp_eviction"] = Counter(
                    "agent_mcp_eviction_total",
                    "MCP server evictions",
                    ["name", "reason"],
                    **({"registry": self._registry} if self._registry is not None else {}),
                )
            except ImportError:
                return
        self._prometheus["mcp_eviction"].labels(name=name[:40], reason=reason[:20]).inc()

    def set_rag_recall_at_k(self, k: int, value: float) -> None:
        if not self._prometheus:
            return
        key = f"rag_recall_at_{k}"
        if key not in self._prometheus:
            try:
                from prometheus_client import Gauge

                self._prometheus[key] = Gauge(
                    f"agent_rag_recall_at_{k}",
                    f"RAG recall at {k}",
                    **({"registry": self._registry} if self._registry is not None else {}),
                )
            except ImportError:
                return
        self._prometheus[key].set(value)

    def observe_rag_faithfulness(self, score: float) -> None:
        if "rag_faithfulness" not in self._histograms:
            try:
                from prometheus_client import Histogram

                self._histograms["rag_faithfulness"] = Histogram(
                    "agent_rag_faithfulness_score",
                    "RAG faithfulness score",
                    buckets=(0.1, 0.3, 0.5, 0.7, 0.8, 0.9, 1.0),
                    **({"registry": self._registry} if self._registry is not None else {}),
                )
            except ImportError:
                return
        self._histograms["rag_faithfulness"].observe(score)

    def inc_embedding_incompatibility(self, stored_model: str, current_model: str) -> None:
        if not self._prometheus:
            return
        if "embedding_incompatibility" not in self._prometheus:
            try:
                from prometheus_client import Counter

                self._prometheus["embedding_incompatibility"] = Counter(
                    "agent_embedding_incompatibility_total",
                    "Embedding index incompatibility detections",
                    ["stored_model", "current_model"],
                    **({"registry": self._registry} if self._registry is not None else {}),
                )
            except ImportError:
                return
        self._prometheus["embedding_incompatibility"].labels(
            stored_model=stored_model[:40],
            current_model=current_model[:40],
        ).inc()

    def observe_db_pool(self, active: int, idle: int, waiting: int) -> None:
        try:
            from prometheus_client import Gauge

            if not hasattr(self, "_db_pool_gauge"):
                self._db_pool_gauge = Gauge(
                    "agent_db_pool_connections",
                    "DB pool connections",
                    ["state"],
                    **({"registry": self._registry} if self._registry is not None else {}),
                )
            self._db_pool_gauge.labels(state="active").set(active)
            self._db_pool_gauge.labels(state="idle").set(idle)
            self._db_pool_gauge.labels(state="waiting").set(waiting)
        except ImportError:
            pass

    def inc_react_loop_entered(self) -> None:
        """Task entered bounded SRDL after planning."""
        self._inc("react_loop_entered")

    def inc_react_action_blocked(self, action: str) -> None:
        if self._prometheus and "react_action_blocked" in self._prometheus:
            self._prometheus["react_action_blocked"].labels(action=action[:40]).inc()

    def record_react_loop_outcome(
        self,
        *,
        status: str,
        exit_path: str,
        step_count: int,
        action_distribution: dict[str, int] | None = None,
        replan_count: int = 0,
        runtime_upgrade: str | None = None,
    ) -> None:
        """Emit Prometheus metrics when an SRDL loop closes."""
        status_key = (status or "unknown")[:20]
        exit_key = (exit_path or "unknown")[:40]
        if status_key == "finished":
            self._inc("react_loop_finished")
        elif status_key == "aborted":
            self._inc("react_loop_aborted")

        if self._prometheus and "react_loop_outcomes" in self._prometheus:
            self._prometheus["react_loop_outcomes"].labels(
                status=status_key, exit_path=exit_key
            ).inc()
        if "react_loop_steps" in self._histograms:
            self._histograms["react_loop_steps"].observe(max(0, step_count))
        if action_distribution and self._prometheus and "react_action_selected" in self._prometheus:
            for action, count in action_distribution.items():
                labels = self._prometheus["react_action_selected"].labels(
                    action=str(action)[:40]
                )
                for _ in range(max(0, int(count))):
                    labels.inc()
        if replan_count > 0 and self._prometheus and "react_replan" in self._prometheus:
            for _ in range(replan_count):
                self._prometheus["react_replan"].inc()
        if runtime_upgrade and self._prometheus and "react_runtime_upgrade" in self._prometheus:
            self._prometheus["react_runtime_upgrade"].labels(
                runtime=str(runtime_upgrade)[:20]
            ).inc()

    def inc_policy_review(self) -> None:
        self._inc("policy_reviews")

    def inc_policy_reject(self) -> None:
        self._inc("policy_rejects")
        self._inc("tasks_rejected")

    def inc_tool_failure(self) -> None:
        self._inc("tool_failures")

    def inc_review_waiting(self) -> None:
        self._inc("reviews_waiting")

    def inc_review_resolved(self) -> None:
        self._inc("reviews_resolved")

    def tenant_metrics_snapshot(self, tenant_id: str) -> dict[str, Any]:
        from app.services.tenant_quota import quota_report

        report = quota_report(tenant_id)
        report["llm_cost_usd"] = self.tenant_llm_cost_usd(tenant_id)
        return report

    def summary(self, *, tenant_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            total_tasks = self._counters.get("tasks_created", 0)
            completed = self._counters.get("tasks_completed", 0)
            reviews = self._counters.get("policy_reviews", 0)
            rejects = self._counters.get("policy_rejects", 0)
            with_retry = self._counters.get("tasks_with_retry", 0)
            failed = self._counters.get("tasks_failed", 0)
            payload: dict[str, Any] = {
                "counters": dict(self._counters),
                "task_completion_rate": (
                    completed / total_tasks if total_tasks else 0.0
                ),
                "task_success_rate": (
                    completed / (completed + failed) if (completed + failed) else 0.0
                ),
                "retry_rate": (
                    with_retry / completed if completed else 0.0
                ),
                "human_review_ratio": (
                    reviews / total_tasks if total_tasks else 0.0
                ),
                "policy_reject_ratio": (
                    rejects / total_tasks if total_tasks else 0.0
                ),
                "tool_failure_count": self._counters.get("tool_failures", 0),
            }
        if tenant_id is not None:
            payload["tenant"] = self.tenant_metrics_snapshot(tenant_id)
        return payload

    def prometheus_text(self) -> str:
        if not settings.METRICS_ENABLED:
            return ""
        try:
            from prometheus_client import generate_latest

            return generate_latest().decode("utf-8")
        except ImportError:
            return ""


_service: MetricsService | None = None


def get_metrics_service() -> MetricsService:
    global _service
    if _service is None:
        _service = MetricsService()
    return _service
