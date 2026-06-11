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
            "skill_invocations": 0,
            "skill_validation_failed": 0,
            "skill_reviews": 0,
        }
        self._skill_stats: dict[str, dict[str, float]] = {}
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
                self._histograms["context_bucket_tokens"] = Histogram(
                    "agent_context_bucket_tokens",
                    "Final tokens per context bucket after governance",
                    ["purpose", "bucket"],
                    buckets=(50, 200, 500, 1000, 2000, 4000, 8000, 16000, 32000),
                    **prom_kwargs,
                )
                self._histograms["context_assembly_latency_ms"] = Histogram(
                    "agent_context_assembly_latency_ms",
                    "Context governance assembly latency (ms)",
                    ["purpose"],
                    buckets=(1, 5, 10, 25, 50, 100, 250, 500),
                    **prom_kwargs,
                )
                self._prometheus["context_drop_total"] = Counter(
                    "agent_context_drop_total",
                    "Context items dropped by governance",
                    ["purpose", "bucket", "reason"],
                    **prom_kwargs,
                )
                self._prometheus["context_compress_total"] = Counter(
                    "agent_context_compress_total",
                    "Context items compressed by governance",
                    ["purpose", "bucket", "method"],
                    **prom_kwargs,
                )
                self._prometheus["context_overflow_prevented_total"] = Counter(
                    "agent_context_overflow_prevented_total",
                    "Global budget overflow prevented",
                    ["purpose"],
                    **prom_kwargs,
                )
                self._prometheus["context_recall_total"] = Counter(
                    "agent_context_recall_total",
                    "Context items recalled from registry/sources",
                    ["source"],
                    **prom_kwargs,
                )
                self._prometheus["context_quality_regression_total"] = Counter(
                    "agent_context_quality_regression_total",
                    "Critical context dropped (quality regression signal)",
                    ["purpose"],
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
                self._prometheus["mission_dispatch_total"] = Counter(
                    "agent_mission_dispatch_total",
                    "OMAW worker dispatches",
                    ["agent", "capability"],
                    **prom_kwargs,
                )
                self._prometheus["fact_bundle_build_total"] = Counter(
                    "agent_fact_bundle_build_total",
                    "FactBundle builds for workers",
                    ["agent", "capability"],
                    **prom_kwargs,
                )
                self._prometheus["fact_bundle_hit_total"] = Counter(
                    "agent_fact_bundle_hit_total",
                    "FactBundle RAG hits by source",
                    ["source_type"],
                    **prom_kwargs,
                )
                self._prometheus["worker_react_enter_total"] = Counter(
                    "agent_worker_react_enter_total",
                    "Bounded ReAct subroutine entries",
                    ["agent", "capability"],
                    **prom_kwargs,
                )
                self._prometheus["worker_react_abort_total"] = Counter(
                    "agent_worker_react_abort_total",
                    "Bounded ReAct aborts",
                    ["agent", "capability", "reason"],
                    **prom_kwargs,
                )
                self._prometheus["review_verdict_total"] = Counter(
                    "agent_review_verdict_total",
                    "ReviewVerdict outcomes",
                    ["qualified"],
                    **prom_kwargs,
                )
                self._prometheus["acceptance_fail_total"] = Counter(
                    "agent_acceptance_fail_total",
                    "OMAW acceptance failures",
                    ["reason"],
                    **prom_kwargs,
                )
                self._prometheus["intent_observation_total"] = Counter(
                    "agent_intent_observation_total",
                    "Intent observation events",
                    ["source", "intent_kind", "session_relation"],
                    **prom_kwargs,
                )
                self._prometheus["intent_observation_fallback_total"] = Counter(
                    "agent_intent_observation_fallback_total",
                    "Intent observation LLM fallbacks",
                    ["reason"],
                    **prom_kwargs,
                )
                self._prometheus["writing_without_fact_bundle_total"] = Counter(
                    "agent_writing_without_fact_bundle_total",
                    "Writing worker runs without FactBundle",
                    **prom_kwargs,
                )
                self._prometheus["review_verdict_missing_fact_bundle_total"] = Counter(
                    "agent_review_verdict_missing_fact_bundle_total",
                    "ReviewVerdict missing fact_bundle_id",
                    **prom_kwargs,
                )
                self._prometheus["mode_resolution_misroute_total"] = Counter(
                    "agent_mode_resolution_misroute_total",
                    "Mode resolution misroute corrections",
                    ["reason"],
                    **prom_kwargs,
                )
                self._prometheus["mission_mechanical_resume_false_positive_total"] = Counter(
                    "agent_mission_mechanical_resume_false_positive_total",
                    "Blocked mechanical resume by intent observation",
                    **prom_kwargs,
                )
                self._prometheus["stay_switch_isolate_disagreement_total"] = Counter(
                    "agent_stay_switch_isolate_disagreement_total",
                    "Shadow structural vs LLM session_relation disagreement",
                    ["structural", "llm"],
                    **prom_kwargs,
                )
                self._prometheus["planning_skip_wrongly_total"] = Counter(
                    "agent_planning_skip_wrongly_total",
                    "Planning incorrectly skipped",
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

    def inc_reasoning_thin_qa_call(self, profile: str, attempt: int) -> None:
        profile_key = (profile or "unknown").strip()[:24] or "unknown"
        self._inc("reasoning_thin_qa_calls")
        self._inc(f"reasoning_thin_qa_calls_{profile_key}_attempt_{attempt}")
        if attempt >= 1:
            self._inc("reasoning_thin_qa_retry_violations")
        if self._prometheus and "reasoning_llm_calls_total" in self._prometheus:
            self._prometheus["reasoning_llm_calls_total"].labels(profile=profile_key).inc()

    def observe_thin_qa_latency(self, seconds: float) -> None:
        self._inc("thin_qa_latency_samples")
        with self._lock:
            bucket = self._counters.setdefault("thin_qa_latency_total_seconds", 0.0)
            self._counters["thin_qa_latency_total_seconds"] = bucket + max(0.0, seconds)
        if self._prometheus and "thin_qa_latency_seconds" in self._prometheus:
            self._prometheus["thin_qa_latency_seconds"].observe(max(0.0, seconds))

    def inc_contract_event(self, kind: str) -> None:
        kind_key = (kind or "unknown").strip().lower()[:40] or "unknown"
        self._inc("contract_events")
        self._inc(f"contract_{kind_key}")

    def _skill_stat_key(self, skill_id: str, version: str) -> str:
        return f"{skill_id[:48]}@{version[:16]}"

    def _bump_skill(self, skill_id: str, version: str, field: str, amount: float = 1.0) -> None:
        key = self._skill_stat_key(skill_id, version)
        with self._lock:
            bucket = self._skill_stats.setdefault(key, {"skill_id": skill_id, "version": version})
            bucket[field] = bucket.get(field, 0) + amount

    def inc_skill_invocation(
        self,
        skill_id: str,
        *,
        version: str = "unknown",
        source_type: str = "unknown",
    ) -> None:
        self._inc("skill_invocations")
        self._bump_skill(skill_id, version, "invocations")
        if self._prometheus:
            self._ensure_skill_prometheus()
            if "skill_invocations" in self._prometheus:
                self._prometheus["skill_invocations"].labels(
                    skill_id=skill_id[:48],
                    version=version[:16],
                    source_type=source_type[:24],
                ).inc()

    def inc_skill_outcome(
        self,
        skill_id: str,
        *,
        version: str = "unknown",
        source_type: str = "unknown",
        outcome: str = "unknown",
    ) -> None:
        self._bump_skill(skill_id, version, f"outcome_{outcome[:20]}")
        if self._prometheus:
            self._ensure_skill_prometheus()
            if "skill_outcomes" in self._prometheus:
                self._prometheus["skill_outcomes"].labels(
                    skill_id=skill_id[:48],
                    version=version[:16],
                    source_type=source_type[:24],
                    outcome=outcome[:20],
                ).inc()

    def inc_skill_validation_failed(
        self,
        skill_id: str,
        *,
        version: str = "unknown",
        count: int = 1,
    ) -> None:
        self._inc("skill_validation_failed", count)
        self._bump_skill(skill_id, version, "validation_failed", count)
        if self._prometheus:
            self._ensure_skill_prometheus()
            if "skill_validation_failed" in self._prometheus:
                self._prometheus["skill_validation_failed"].labels(
                    skill_id=skill_id[:48],
                    version=version[:16],
                ).inc(count)

    def inc_skill_review(self, skill_id: str, *, version: str = "unknown") -> None:
        self._inc("skill_reviews")
        self._bump_skill(skill_id, version, "reviews")
        if self._prometheus:
            self._ensure_skill_prometheus()
            if "skill_reviews" in self._prometheus:
                self._prometheus["skill_reviews"].labels(
                    skill_id=skill_id[:48],
                    version=version[:16],
                ).inc()

    def _ensure_skill_prometheus(self) -> None:
        if not self._prometheus:
            return
        if "skill_invocations" in self._prometheus:
            return
        try:
            from prometheus_client import Counter

            prom_kwargs: dict[str, Any] = {}
            if self._registry is not None:
                prom_kwargs["registry"] = self._registry
            self._prometheus["skill_invocations"] = Counter(
                "agent_skill_invocations_total",
                "Tasks started with a skill",
                ["skill_id", "version", "source_type"],
                **prom_kwargs,
            )
            self._prometheus["skill_outcomes"] = Counter(
                "agent_skill_task_outcomes_total",
                "Task outcomes for skill-driven runs",
                ["skill_id", "version", "source_type", "outcome"],
                **prom_kwargs,
            )
            self._prometheus["skill_validation_failed"] = Counter(
                "agent_skill_output_validation_failed_total",
                "Skill output contract validation failures",
                ["skill_id", "version"],
                **prom_kwargs,
            )
            self._prometheus["skill_reviews"] = Counter(
                "agent_skill_review_total",
                "Human reviews on skill-driven tasks",
                ["skill_id", "version"],
                **prom_kwargs,
            )
            self._prometheus["skill_tool_usage"] = Counter(
                "agent_skill_tool_usage_total",
                "Tool invocations on skill-driven tasks",
                ["skill_id", "version", "tool"],
                **prom_kwargs,
            )
        except ImportError:
            pass

    def inc_skill_tool_usage(
        self,
        skill_id: str,
        *,
        tool_name: str,
        version: str = "unknown",
        count: int = 1,
    ) -> None:
        key = f"tool_{skill_id[:32]}_{tool_name[:24]}"
        self._inc(key, count)
        self._bump_skill(skill_id, version, f"tool_{tool_name[:24]}", count)
        if self._prometheus:
            self._ensure_skill_prometheus()
            if "skill_tool_usage" in self._prometheus:
                self._prometheus["skill_tool_usage"].labels(
                    skill_id=skill_id[:48],
                    version=version[:16],
                    tool=tool_name[:40],
                ).inc(count)

    def skill_metrics_summary(self) -> dict[str, Any]:
        with self._lock:
            by_skill = [dict(v) for v in self._skill_stats.values()]
            tool_usage = {
                k: v
                for k, v in self._counters.items()
                if k.startswith("tool_") and "/" not in k
            }
        return {
            "skill_invocations": self._counters.get("skill_invocations", 0),
            "skill_validation_failed": self._counters.get("skill_validation_failed", 0),
            "skill_reviews": self._counters.get("skill_reviews", 0),
            "tool_usage_counters": tool_usage,
            "by_skill": sorted(by_skill, key=lambda x: str(x.get("skill_id", ""))),
        }

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

    def observe_context_bucket_tokens(
        self, purpose: str, bucket: str, tokens: float
    ) -> None:
        key = f"context_bucket_{purpose}_{bucket}"
        with self._lock:
            self._counters[key] = float(tokens)
        if "context_bucket_tokens" in self._histograms:
            self._histograms["context_bucket_tokens"].labels(
                purpose=(purpose or "unknown")[:24],
                bucket=(bucket or "unknown")[:24],
            ).observe(float(tokens))

    def inc_context_drop(self, purpose: str, bucket: str, reason: str) -> None:
        key = f"context_drop_{purpose}_{bucket}"
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + 1
        if self._prometheus and "context_drop_total" in self._prometheus:
            self._prometheus["context_drop_total"].labels(
                purpose=(purpose or "unknown")[:24],
                bucket=(bucket or "unknown")[:24],
                reason=(reason or "unknown")[:32],
            ).inc()

    def inc_context_compress(self, purpose: str, bucket: str, method: str) -> None:
        key = f"context_compress_{purpose}_{bucket}_{method}"
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + 1
        if self._prometheus and "context_compress_total" in self._prometheus:
            self._prometheus["context_compress_total"].labels(
                purpose=(purpose or "unknown")[:24],
                bucket=(bucket or "unknown")[:24],
                method=(method or "unknown")[:24],
            ).inc()

    def inc_context_overflow_prevented(self, purpose: str) -> None:
        key = f"context_overflow_prevented_{purpose}"
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + 1
        if self._prometheus and "context_overflow_prevented_total" in self._prometheus:
            self._prometheus["context_overflow_prevented_total"].labels(
                purpose=(purpose or "unknown")[:24],
            ).inc()

    def observe_context_assembly_latency_ms(self, purpose: str, elapsed_ms: float) -> None:
        if "context_assembly_latency_ms" in self._histograms:
            self._histograms["context_assembly_latency_ms"].labels(
                purpose=(purpose or "unknown")[:24],
            ).observe(max(0.0, float(elapsed_ms)))

    def inc_context_recall(self, source: str, count: int = 1) -> None:
        key = f"context_recall_{source}"
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + max(1, int(count))
        if self._prometheus and "context_recall_total" in self._prometheus:
            self._prometheus["context_recall_total"].labels(
                source=(source or "unknown")[:24],
            ).inc(max(1, int(count)))

    def inc_context_quality_regression(self, purpose: str) -> None:
        key = f"context_quality_regression_{purpose}"
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + 1
        if self._prometheus and "context_quality_regression_total" in self._prometheus:
            self._prometheus["context_quality_regression_total"].labels(
                purpose=(purpose or "unknown")[:24],
            ).inc()

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

    def observe_rag_retrieval_noise(self, stats: dict[str, float | int]) -> None:
        """Record retrieval noise observability: filter pass rate, adjacency ratio, etc."""
        if not self._prometheus:
            return
        gauges = (
            ("retrieved_total", "agent_rag_retrieved_total"),
            ("threshold_passed", "agent_rag_threshold_passed"),
            ("injected_knowledge", "agent_rag_injected_knowledge"),
            ("adjacency_count", "agent_rag_adjacency_count"),
            ("adjacency_ratio", "agent_rag_adjacency_ratio"),
            ("low_score_injected_rate", "agent_rag_low_score_injected_rate"),
        )
        for key, metric_name in gauges:
            if key not in stats:
                continue
            if metric_name not in self._prometheus:
                try:
                    from prometheus_client import Gauge

                    self._prometheus[metric_name] = Gauge(
                        metric_name,
                        f"RAG retrieval noise: {key}",
                        **({"registry": self._registry} if self._registry is not None else {}),
                    )
                except ImportError:
                    return
            self._prometheus[metric_name].set(float(stats[key]))

    def observe_retrieval_trace(self, trace: dict[str, object]) -> None:
        """Record evidence pipeline per-turn trace metrics."""
        if not self._prometheus:
            return
        purpose = str(trace.get("purpose") or "unknown")[:24]
        counters = (
            ("candidate_count", "agent_retrieval_candidates"),
            ("admitted_count", "agent_retrieval_admitted"),
            ("injected_count", "agent_retrieval_injected"),
        )
        for key, metric_name in counters:
            if key not in trace:
                continue
            if metric_name not in self._prometheus:
                try:
                    from prometheus_client import Gauge

                    self._prometheus[metric_name] = Gauge(
                        metric_name,
                        f"Retrieval trace: {key}",
                        ["purpose"],
                        **({"registry": self._registry} if self._registry is not None else {}),
                    )
                except ImportError:
                    return
            self._prometheus[metric_name].labels(purpose=purpose).set(float(trace[key]))  # type: ignore[arg-type]

        for tag in trace.get("failure_tags") or []:
            self.inc_retrieval_failure_tag(str(tag), purpose=purpose)

    def inc_retrieval_failure_tag(self, tag: str, *, purpose: str = "unknown") -> None:
        key = f"retrieval_failure_{tag}"
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + 1
        if self._prometheus and "retrieval_failure_total" in self._prometheus:
            self._prometheus["retrieval_failure_total"].labels(
                tag=tag[:32], purpose=purpose[:24]
            ).inc()
        elif self._prometheus is not None:
            try:
                from prometheus_client import Counter

                self._prometheus["retrieval_failure_total"] = Counter(
                    "agent_retrieval_failure_total",
                    "Retrieval failure taxonomy tags",
                    ["tag", "purpose"],
                    **({"registry": self._registry} if self._registry is not None else {}),
                )
                self._prometheus["retrieval_failure_total"].labels(
                    tag=tag[:32], purpose=purpose[:24]
                ).inc()
            except ImportError:
                pass

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

    def inc_mission_dispatch(self, agent: str, capability: str) -> None:
        key = f"oma_dispatch:{agent}:{capability}"
        self._counters[key] = self._counters.get(key, 0) + 1
        if self._prometheus and "mission_dispatch_total" in self._prometheus:
            self._prometheus["mission_dispatch_total"].labels(
                agent=agent[:20], capability=capability[:30]
            ).inc()

    def inc_fact_bundle_build(self, agent: str, capability: str) -> None:
        key = f"oma_fact_bundle:{agent}:{capability}"
        self._counters[key] = self._counters.get(key, 0) + 1
        if self._prometheus and "fact_bundle_build_total" in self._prometheus:
            self._prometheus["fact_bundle_build_total"].labels(
                agent=agent[:20], capability=capability[:30]
            ).inc()

    def inc_fact_bundle_hit(self, source_type: str) -> None:
        key = f"oma_fact_hit:{source_type}"
        self._counters[key] = self._counters.get(key, 0) + 1
        if self._prometheus and "fact_bundle_hit_total" in self._prometheus:
            self._prometheus["fact_bundle_hit_total"].labels(
                source_type=source_type[:30]
            ).inc()

    def inc_worker_react_enter(self, agent: str, capability: str) -> None:
        key = f"oma_react_enter:{agent}:{capability}"
        self._counters[key] = self._counters.get(key, 0) + 1
        if self._prometheus and "worker_react_enter_total" in self._prometheus:
            self._prometheus["worker_react_enter_total"].labels(
                agent=agent[:20], capability=capability[:30]
            ).inc()

    def inc_worker_react_abort(self, agent: str, capability: str, reason: str) -> None:
        key = f"oma_react_abort:{reason}"
        self._counters[key] = self._counters.get(key, 0) + 1
        if self._prometheus and "worker_react_abort_total" in self._prometheus:
            self._prometheus["worker_react_abort_total"].labels(
                agent=agent[:20], capability=capability[:30], reason=reason[:40]
            ).inc()

    def inc_review_verdict(self, qualified: bool) -> None:
        label = "true" if qualified else "false"
        key = f"oma_verdict:{label}"
        self._counters[key] = self._counters.get(key, 0) + 1
        if self._prometheus and "review_verdict_total" in self._prometheus:
            self._prometheus["review_verdict_total"].labels(qualified=label).inc()

    def inc_acceptance_fail(self, reason: str) -> None:
        key = f"oma_acceptance_fail:{reason[:40]}"
        self._counters[key] = self._counters.get(key, 0) + 1
        if self._prometheus and "acceptance_fail_total" in self._prometheus:
            self._prometheus["acceptance_fail_total"].labels(reason=reason[:40]).inc()

    def inc_intent_observation(
        self,
        *,
        source: str,
        intent_kind: str,
        session_relation: str,
    ) -> None:
        key = f"intent_obs:{source}:{intent_kind}"
        self._counters[key] = self._counters.get(key, 0) + 1
        if self._prometheus and "intent_observation_total" in self._prometheus:
            self._prometheus["intent_observation_total"].labels(
                source=source[:20],
                intent_kind=intent_kind[:30],
                session_relation=session_relation[:20],
            ).inc()

    def inc_intent_observation_fallback(self, reason: str) -> None:
        key = f"intent_obs_fallback:{reason[:40]}"
        self._counters[key] = self._counters.get(key, 0) + 1
        if self._prometheus and "intent_observation_fallback_total" in self._prometheus:
            self._prometheus["intent_observation_fallback_total"].labels(
                reason=reason[:40]
            ).inc()

    def inc_writing_without_fact_bundle(self) -> None:
        self._inc("writing_without_fact_bundle")
        if self._prometheus and "writing_without_fact_bundle_total" in self._prometheus:
            self._prometheus["writing_without_fact_bundle_total"].inc()

    def inc_review_verdict_missing_fact_bundle(self) -> None:
        self._inc("review_verdict_missing_fact_bundle")
        if (
            self._prometheus
            and "review_verdict_missing_fact_bundle_total" in self._prometheus
        ):
            self._prometheus["review_verdict_missing_fact_bundle_total"].inc()

    def inc_mode_resolution_misroute(self, reason: str) -> None:
        key = f"misroute:{reason[:40]}"
        self._counters[key] = self._counters.get(key, 0) + 1
        if self._prometheus and "mode_resolution_misroute_total" in self._prometheus:
            self._prometheus["mode_resolution_misroute_total"].labels(
                reason=reason[:40]
            ).inc()

    def inc_mechanical_resume_blocked(self) -> None:
        self._inc("mechanical_resume_blocked")
        if (
            self._prometheus
            and "mission_mechanical_resume_false_positive_total" in self._prometheus
        ):
            self._prometheus["mission_mechanical_resume_false_positive_total"].inc()

    def inc_stay_switch_isolate_disagreement(
        self, structural: str, llm: str
    ) -> None:
        key = f"session_disagree:{structural}:{llm}"
        self._counters[key] = self._counters.get(key, 0) + 1
        if (
            self._prometheus
            and "stay_switch_isolate_disagreement_total" in self._prometheus
        ):
            self._prometheus["stay_switch_isolate_disagreement_total"].labels(
                structural=structural[:20], llm=llm[:20]
            ).inc()

    def inc_planning_skip_wrongly(self) -> None:
        self._inc("planning_skip_wrongly")
        if self._prometheus and "planning_skip_wrongly_total" in self._prometheus:
            self._prometheus["planning_skip_wrongly_total"].inc()

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

    def inc_task_control_event(self, kind: str) -> None:
        key = f"task_control_{(kind or 'unknown')[:40]}"
        self._inc(key)
        self.inc_contract_event(kind)

    def observe_pause_latency_ms(self, latency_ms: int) -> None:
        self._inc("pause_latency_samples")
        self._counters["pause_latency_ms_total"] = (
            self._counters.get("pause_latency_ms_total", 0) + max(0, int(latency_ms))
        )

    def observe_cancel_latency_ms(self, latency_ms: int) -> None:
        self._inc("cancel_latency_samples")
        self._counters["cancel_latency_ms_total"] = (
            self._counters.get("cancel_latency_ms_total", 0) + max(0, int(latency_ms))
        )

    def observe_checkpoint_commit_ms(self, latency_ms: int) -> None:
        self._inc("checkpoint_commit_samples")
        self._counters["checkpoint_commit_ms_total"] = (
            self._counters.get("checkpoint_commit_ms_total", 0) + max(0, int(latency_ms))
        )

    def inc_partial_commit(self) -> None:
        self._inc("partial_commit_count")

    def inc_resume_from_checkpoint(self) -> None:
        self._inc("resume_from_checkpoint_count")

    def inc_structured_checkpoint_restore(self, *, success: bool, reason: str = "unknown") -> None:
        if success:
            self._inc("structured_checkpoint_restore_success")
        else:
            self._inc("structured_checkpoint_restore_failure")
        reason_key = (reason or "unknown").strip().lower()[:24] or "unknown"
        self._inc(f"structured_checkpoint_restore_{reason_key}")

    def inc_disconnect_without_cancel(self) -> None:
        self._inc("disconnect_without_cancel_count")

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
        payload["skills"] = self.skill_metrics_summary()
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
