# Capability maturity matrix

> Stable / beta / experimental boundaries for agent-langraph  
> **实现状态明细**：[`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md)

| Capability | Maturity | Default | Status | Notes |
|------------|----------|---------|--------|-------|
| Single-turn graph (`execution_mode=single`) | **stable** | on | ✅ | Primary path; full test coverage |
| Session / multi-turn | **stable** | on | ✅ | `session.enabled=true`；`conversation_context.py` |
| Session history persistence | **stable** | on | ✅ | user+assistant 同步；审核中断草稿入库 |
| Session memory retrieval | **stable** | on | ✅ | `session.memory_retrieval_enabled`；`retrieval_policy.py` |
| Segment-aware output guard | **stable** | on | ✅ | `content_segments`；`output_guard.pii_prose_only` |
| Reasoning code artifacts | **stable** | on | ✅ | `structured.artifacts` + `answer_compose` |
| Code compile verify + stderr repair | **stable** | on | ✅ | [`CODE_ARTIFACT_PIPELINE.md`](CODE_ARTIFACT_PIPELINE.md)；`code_verify` + `repair`（无排版硬编码） |
| Multi-turn reasoning isolation | **stable** | on | ✅ | [`REASONING_SHORTCUT.md`](REASONING_SHORTCUT.md) |
| Route audit (post-planning) | **stable** | on | ✅ | [`ROUTE_AUDIT.md`](ROUTE_AUDIT.md)；`config route_audit` |
| Reflection + replan on misroute | **stable** | on | ✅ | `retry_planning`；`reflection.route_audit_on_misroute` |
| Artifact content profiles | **stable** | on | ✅ | `source_code` / `manuscript_prose` / `outline` in `artifact_content` |
| Mission / longform | **beta** | opt-in | ✅ | `mission` / planning handoff；`autonomous` 连续多步 |
| Mission writing phases (LLM decide) | **beta** | on* | ✅ | `mission.writing_llm_decide`；`writing_phase` 写/审/润/摘要；见 `MANUSCRIPT_WRITING.md` |
| Supervisor multi-agent | **beta** | opt-in | ✅ | `task_type=supervisor`；integration golden |
| Exploration graph | **beta** | opt-in | ✅ | `execution_mode=exploration` |
| Reflection node | **stable** | on | ✅ | 事实复核 + 路由错位 `retry_planning`（`writing_only` 默认 false） |
| RAG (Chroma/Qdrant + RRF) | **stable** | on | ✅ | Rerank: lexical / cross_encoder / cohere |
| RAG eval (citation / faithfulness) | **beta** | off | ✅ | `rag.*` flags；CI `SUITE=rag` |
| Golden eval (unit + integration + rag) | **stable** | on | ✅ | 26 cases；`scripts/ci_eval.sh` |
| Graph execution backpressure | **stable** | on | ✅ | `graph_runner.backpressure_enabled` |
| API rate limit | **stable** | on | ✅ | `rate_limit.enabled` |
| LLM circuit breaker | **beta** | off | ✅ | `llm.circuit_breaker_enabled` |
| Checkpoint corruption recovery | **beta** | off | ✅ | `checkpoint_recovery.py` |
| Context compress (character) | **stable** | on | ✅ | `session.compress_enabled` |
| Semantic context compress | **beta** | off | 🔶 | Code ✅；E2E/CI ratio ❌ |
| Skill runtime | **beta** | off | ✅ | `skill.enabled=false`；registry + yaml |
| MCP tools + ops | **beta** | off | ✅ | probe/evict；resources/prompts；HTTP stub E2E |
| Embedding governance | **stable** | on | ✅ | meta table + reindex + compatibility check |
| Multi-tenant storage + quota | **beta** | off | ✅ | PG schema / SQLite 分库；`POST /tenants` |
| LLM cost per tenant | **beta** | on* | ✅ | Prometheus + `/metrics/tenant`（*进程内累计） |
| AgentState Pydantic | **beta** | partial | 🔶 | `state_store` save 校验；图内仍 TypedDict |
| A2A registry + HTTP forward | **beta** | on | ✅ | `a2a.http_forward_enabled` |
| LangSmith tracing | **experimental** | off | ✅ | `observability.langsmith_enabled` |
| LangGraphics live UI | **experimental** | off | ✅ | `langgraphics.enabled` |
| Celery queue | **beta** | off | ✅ | `queue.backend=memory` default |
| OCR / multimodal anti-hallucination | **experimental** | — | ❌ | 未实现 |
| PG HA (Patroni/pgpool) | **experimental** | — | ❌ | 无 failover 集成测试 |
| Edge LLM / GDPR / DR | **experimental** | — | ❌ | 长期 |
| Controlled file edit tool | **stable** | on | ✅ | Whitelist + `dry_run` + audit |

\* `writing_llm_decide` 默认 `true`（`config.yaml`）；关闭后回退规则型 `suggest_writing_phase_fallback`。

## Recommended profiles

### Local demo (no API keys)

```bash
export MODEL_ENABLED=false
bash scripts/demo_local.sh
```

### Production (real LLM)

```bash
export MODEL_ENABLED=true
export ANTHROPIC_API_KEY=...
# Optional: VOYAGE_API_KEY for embeddings
```

### Long-form writing (Mission)

```yaml
mission:
  writing_llm_decide: true
  steps_hard_cap: 500
  writing_max_steps: 500
# payload example:
# mission: { kind: writing, total_target_chars: 1200000, autonomous: true,
#   step_policy: { chars_per_step: 4000, first_step: outline, then: append_body } }
```

### Hardened

```yaml
llm:
  circuit_breaker_enabled: true
context_compress:
  semantic_enabled: true
rag:
  rerank_enabled: true
  faithfulness_check_enabled: true
graph_runner:
  max_concurrent: 16
checkpoint:
  auto_reset_on_corrupt: true  # only after ops review
```

### Multi-tenant (v0.13)

```yaml
tenant:
  enabled: true
  max_tasks_per_day: 1000
  max_tokens_per_day: 500000
  max_concurrent_tasks: 10
```

```bash
bash scripts/create_tenant_storage.sh create acme
# Requests: header X-Tenant-Id: acme
```
