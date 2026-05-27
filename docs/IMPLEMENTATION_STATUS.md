# 实现状态追踪（对齐代码库）

> **最后更新**：2026-05-27（补：display/delivery 管线、artifact 流式、memory writeback 门控、生效计划 trace）  
> **对照文档**：[`CAPABILITY_MATRIX.md`](CAPABILITY_MATRIX.md)  
> **图例**：✅ 已实现并有用例覆盖 · 🔶 部分实现 / 默认关 · ❌ 未实现

当前代码库处于 **v0.11–v0.12 能力已落地、v0.13 平台化进行中** 状态（以仓库实测为准，非发布 tag）。

---

## 一、批次总览

| Batch | 主题 | 状态 | 说明 |
|-------|------|------|------|
| **0** | 开源可用性 | ✅ **完成** | README 5 分钟路径、`scripts/demo_local.sh`、`CAPABILITY_MATRIX.md` |
| **1** | 可信问答 | ✅ **完成** | RAG 闭环 + Golden 26 项；语义压缩 E2E + `agent_context_compress_ratio` 门禁 |
| **2** | 稳定性 | ✅ **完成** | 背压、熔断、Checkpoint 恢复 |
| **3** | 能力模块化 | 🔶 **核心完成** | Skill、MCP 运维、Embedding 治理；OCR 未做 |
| **4** | 平台化 | 🔶 **核心完成** | 租户隔离/配额/成本/API；图边界 Pydantic + mypy；PG HA 未做 |

---

## 二、Batch 0 — 开源可用性

| ID | 任务 | 状态 | 产物 |
|----|------|------|------|
| B0-1 | 5 分钟快速路径 | ✅ | `README.md` §零 API Key |
| B0-2 | `demo_local.sh` | ✅ | `scripts/demo_local.sh` |
| B0-3 | 能力矩阵 | ✅ | `docs/CAPABILITY_MATRIX.md` |
| B0-4 | 受控文件编辑文档 | 🔶 | 架构/工具存在；README 专节可再补 |

---

## 三、Batch 1 — 可信问答

### 1.1 语义上下文压缩

| 项 | 状态 | 说明 |
|----|------|------|
| `context_compressor.py` | ✅ | `SemanticContextSummary`、字符/语义双路径 |
| `conversation_context` 模块 | ✅ | history 读写、`finalize_turn_history`、草稿 answer、memory 写回 |
| `session_turn` 接入 | ✅ | `prepare_session_turn`；压缩见 `compress_session_history()` |
| 配置 `session.memory_retrieval_enabled` | ✅ | 多轮 QA 在 `skip_retrieval` 时仍走 session memory |
| 配置 `context_compress.semantic_enabled` | ✅ | 默认 **false** |
| 单测 | ✅ | `tests/services/test_context_compressor.py` |
| E2E | ❌ | `test_context_compress_e2e.py` 未建 |
| CI 压缩率门禁 | ❌ | `agent_context_compress_ratio` 未接 Prometheus |
| DoD（开源可用性批次） | 🔶 | 逻辑有，门禁与 E2E 未齐 |

### 1.2 RAG 精排 + Citation + Faithfulness

| 项 | 状态 | 说明 |
|----|------|------|
| `reranker.py` | ✅ | lexical / cross_encoder / cohere |
| `rag_eval.py` | ✅ | citation、启发式 + 可选 LLM faithfulness |
| `rag_metrics.py` | ✅ | Recall@k、MRR、NDCG |
| 节点接入 | ✅ | retrieval / output / output_guard / fact_layer |
| 配置 `rag.*` | ✅ | rerank、citation、faithfulness 默认关 |
| 单测 | ✅ | `test_reranker.py`、`test_rag_eval.py` |
| Eval | ✅ | `test_rag_golden.py`（8 任务）、`test_rag_faithfulness_llm.py`（mock LLM） |
| CI | ✅ | `scripts/ci_eval.sh` SUITE=rag；阈值 `eval_thresholds.py` |
| Faithfulness 生产 LLM 实测 | 🔶 | CI 仅 mock；live 需 API Key |

### 1.3 Golden 评测集

| 层级 | 目标 | 实际 | 状态 |
|------|------|------|------|
| Unit | 8+ | 8 | ✅ `tests/eval/baseline.json` |
| Integration | 8+ | **10** | ✅ `integration_cases.json` + `integration_baseline.json` |
| RAG | 8+ | **8** | ✅ `rag_golden_tasks.yaml` + `rag_baseline.json` |
| 合计 | 20+ | **26** | ✅ |
| `test_golden_quality` | — | ✅ | doc_id 存在性校验 |
| `ci_eval.sh` | unit/integration/rag/all | ✅ | |
| `.github/workflows/eval.yml` | — | ✅ | 分 suite 步骤 |
| Mission planning handoff | — | ✅ | `enable_planning_mission_handoff` + sync stream handoff |
| 通过率门禁 85%/80% | — | 🔶 | baseline 回归有；汇总通过率未单独 gate |

---

## 四、Batch 2 — 稳定性

| 项 | 状态 | 关键文件 / 测试 |
|----|------|-----------------|
| 图执行背压 | ✅ | `graph_execution_pool.py`；429 `GraphExecutionRejected`；`test_graph_runner_backpressure.py` |
| Prometheus 队列指标 | ✅ | `graph_queue_active` / `graph_rejected_total` 等 |
| LLM Circuit Breaker | ✅ | `circuit_breaker.py`；`llm.circuit_breaker_enabled`；`test_circuit_breaker.py` |
| LLM 集成熔断 E2E | ❌ | `test_llm_circuit_breaker.py` 未建 |
| Checkpoint 恢复 | ✅ | `checkpoint_recovery.py`；`test_checkpoint_recovery.py` |
| 压测脚本 | ❌ | 文档级待补 |

---

## 五、Batch 3 — 能力模块化

| 项 | 状态 | 说明 |
|----|------|------|
| Skill 运行时 | ✅ | `skill.py`、`skill_registry.py`、`config/skills/`；`test_skill_registry` |
| MCP 运维 | ✅ | `mcp_manager.py`；probe/evict；resources/prompts；`test_mcp_manager.py` |
| MCP HTTP E2E | ✅ | `mcp_stubs/http_server.py`、`test_mcp_http_e2e.py` |
| Embedding 治理 | ✅ | `embedding_meta.py`、`embedding_reindex.py`；`test_embedding_governance.py` |
| OCR 反幻觉 | ❌ | `ocr_guard.py` 未建 |

---

## 六、Batch 4 — 平台化

| ID | 任务 | 状态 | 说明 |
|----|------|------|------|
| B4-1a | PG `search_path` 租户 schema | ✅ | `db.py`：`create_tenant_schema` / `drop_tenant_schema`；连接自动 `SET search_path` |
| B4-1b | SQLite 分库 | ✅ | `tenant_storage.py`：`tenant_{id}.db` + 独立 vectorstore 目录 |
| B4-1c | Store 按租户路由 | ✅ | state/audit/memory/knowledge/DLQ getters |
| B4-1d | 租户配额 | ✅ | `tenant_quota.py`；graph_runner 入口；429 |
| B4-1e | 知识库 metadata 隔离 | ✅ | `tenant_id` in metadata + search 过滤 |
| B4-1f | 租户管理 API | ✅ | `POST/DELETE /tenants`；`GET .../quota` |
| B4-1g | 运维脚本 | ✅ | `scripts/create_tenant_storage.sh` |
| B4-2 | LLM 成本归因 | ✅ | `agent_llm_cost_usd_total`；进程内 `tenant_llm_cost_usd`；`GET /metrics/tenant` |
| B4-3a | AgentState Pydantic 第一阶段 | ✅ | `agent_state_model.py`；`state_store.save` 校验 |
| B4-3b | 全图节点边界 Pydantic | ✅ | `ensure_agent_state`：`merge_state` + 各 graph invoke/stream |
| B4-3c | mypy strict | ✅ | `mypy.ini` + `scripts/ci_mypy.sh`（`app/runtime`） |
| B4-4 | Patroni/pgpool failover 测试 | ❌ | 需 HA 环境 |
| B4-5 | Edge LLM / GDPR / DR | ❌ | 长期 |

### 多租户启用速查

```yaml
# config/config.yaml
tenant:
  enabled: true
  max_tasks_per_day: 1000
  max_tokens_per_day: 500000
  max_concurrent_tasks: 10
```

```bash
# 创建租户存储（admin）
curl -X POST http://localhost:8000/tenants \
  -H "X-User-Role: admin" -H "Content-Type: application/json" \
  -d '{"tenant_id":"acme"}'

# 业务请求
curl -H "X-Tenant-Id: acme" http://localhost:8000/tasks ...

# 配额与成本
curl -H "X-Tenant-Id: acme" http://localhost:8000/metrics/tenant
```

---

## 七、长篇 Mission 写作（Manuscript + Orchestration）

| 项 | 状态 | 说明 |
|----|------|------|
| 手稿子系统 | ✅ | `manuscript_service`、`writing_node`、`build_writing_context` |
| Planning → Mission handoff | ✅ | `enable_planning_mission_handoff` + `mission_auto` |
| Lazy work_plan | ✅ | `mission_orchestrator` 按 `step_policy` 推进一步 |
| **写作阶段（模型自选）** | ✅ | `writing_phases.py`；`mission.writing_llm_decide`（默认 true） |
| `writing_phase` 阶段 | ✅ | outline / append / consistency / review / polish / summary / arc_checkpoint |
| `story_bible.json` / `chapter_reviews.json` | ✅ | 任务 artifact 目录侧车文件 |
| **autonomous 连续执行** | ✅ | `init_mission_state` 保留 autonomous；`stepwise_pause` 对 autonomous 为 false |
| 输出保留 `MISSION_PAUSED` | ✅ | `output_node._resolve_output_status` |
| Web steer + 输入不禁用 | ✅ | `POST .../steer`；`web/static/app.js` |
| Steer intent 确认（规划后、执行前） | ✅ | `mission_steer_confirm.py`；`planning_node` |
| Steer outcome 确认（工作项后、节选） | ✅ | `confirmation/` 预览策略 + `mission_steer_outcome_confirm.py`；见 [`CONFIRMATION_GATES.md`](CONFIRMATION_GATES.md) |
| Steer replan / work_plan SSOT | ✅ | `mission/steer_replan.py`；planning `work_plan_patch` |
| 预览策略（head/tail/diff/delta） | ✅ | `confirmation/preview_resolver.py`；`config.confirmation_gates` |
| 结构化批准 `confirm:true` | ✅ | `resume` / `steer` API；`steer_confirmation_actions.py`；无 NL 短语表 |
| Steer payload 持久化 | ✅ | `state_store._PAYLOAD_VOLATILE_KEYS` |
| Steer 优先级 / 抢占提示 | ✅ | `POST /tasks/{id}/steer` 支持 `priority`/`preempt`；写作生成前 best-effort 收敛 |
| Session turn 规划闸门 | ✅ | `session_turn.py`；`graph_runner._prepare_mission_for_turn` |
| 客户端展示 `client_display` | ✅ | `system_lines` / `autonomous_ui`；Web 不硬编码 steer_action 文案 |
| Streaming 合并修复（避免 work_plan 回滚） | ✅ | `merge_state` 深合并 `progress/input_payload`；`stream_mission_graph` 使用 merge |
| 纲文对齐决策（重写大纲后自动判断是否 reset_body） | ✅ | `outline_body_alignment.py` + `writing_node` 写入 forced `reset_body` |
| Reasoning JSON 修复链路（repair + fallback） | ✅ | `extract_json_with_repair`；reasoning 流式/非流式统一走修复 |
| 多轮会话 history 持久化（user+assistant 同步） | ✅ | `conversation_context.py`；`finalize_turn_history` 合并 payload/state |
| 审核中断轮次草稿入库 | ✅ | `persist_turn_draft_answer` + `resolve_turn_surface_answer` |
| Session memory 检索（skip_retrieval 时） | ✅ | `retrieval_policy.py`；`router` + `retrieval_node` |
| Parser fallback 不触发低置信度审核 | ✅ | `policy_engine._is_parser_format_recovery` |
| PG `memories.session_id` 迁移 | ✅ | `db.POSTGRES_SCHEMA_MIGRATIONS` |
| Segment-aware output_guard（代码块不扫 PII） | ✅ | `content_segments.py` + `output_guard.pii_prose_only` |
| Reasoning `structured.artifacts` + answer 组装 | ✅ | `answer_compose.py`；`output_node` / `persist_turn_draft_answer` |
| History outcome / session_outcomes_digest | ✅ | `conversation_context`；拒答不入 LLM history |
| Memory 检索 query 增强 + 指标 | ✅ | `memory_query.py`；`session_memory_*` counters |
| Route audit（规划后路径审计） | ✅ | [`ROUTE_AUDIT.md`](ROUTE_AUDIT.md)；`app/services/route_audit/` |
| Reflection 路由纠错 + replan | ✅ | `retry_planning`；`reflection.route_audit_on_misroute` |
| Display / delivery（缩进、流式、代码写盘） | ✅ | [`DISPLAY_AND_DELIVERY.md`](DISPLAY_AND_DELIVERY.md)；`answer_compose` / `delivery_policy` |
| Code artifact pipeline（编译校验 / stderr 修复 / 流式） | ✅ | [`CODE_ARTIFACT_PIPELINE.md`](CODE_ARTIFACT_PIPELINE.md)；`code_artifact_pipeline.py` + `code_verify/`（无排版硬编码、无压扁启发式） |
| Memory writeback 门控 | ✅ | `memory.writeback`；`memory_writeback_policy.py` |
| Artifact 生成 profile | ✅ | `artifact_profile`：source_code / manuscript_prose / outline |
| 多轮 reasoning 隔离 | ✅ | [`REASONING_SHORTCUT.md`](REASONING_SHORTCUT.md)；`reasoning_shortcut.py` |
| Reasoning 修复可观测性 | ✅ | audit 记录 `parser_repaired/parser_fallback`；metrics 计数 `reasoning_parser_*` |
| `budget.max_steps` 估算 + 硬顶 500 | ✅ | `mission_schema.resolve_mission_budget_dict` |
| Supervisor 按章多 Agent | ❌ | 未与 mission 写作默认打通；见 `MANUSCRIPT_WRITING.md` |
| 单测 | ✅ | `test_writing_phases.py`、`test_mission_autonomous_orchestration.py`、`test_mission_budget_steps.py` |

配置见 `config/config.yaml` → `mission.writing_llm_decide`、`mission.steps_hard_cap`。

---

## 八、验收总表

| 缺口项 | 计划版本 | 实现 | 测试 | CI 门禁 |
|--------|----------|------|------|---------|
| 语义上下文压缩 | v0.11 | ✅ | ✅ E2E | ✅ ratio ≥ `min_ratio` |
| RAG 精排+citation+faithfulness | v0.11 | ✅ | ✅ | ✅ rag suite |
| Golden 20+ | v0.11 | ✅ 26 项 | ✅ | ✅ baseline 回归 |
| 背压队列 | v0.11.1 | ✅ | ✅ | 🔶 指标有/压测无 |
| Circuit breaker | v0.11.1 | ✅ | ✅ 单测 | 🔶 无集成 E2E |
| Checkpoint 恢复 | v0.11.1 | ✅ | ✅ | — |
| Skill 运行时 | v0.12 | ✅ | ✅ | — |
| Embedding 治理 | v0.12 | ✅ | ✅ | — |
| OCR 反幻觉 | v0.12 | ❌ | ❌ | ❌ |
| 多租户 | v0.13 | ✅ | ✅ quota+isolation | — |
| AgentState Pydantic | v0.13 | ✅ 图边界 | ✅ model | ✅ `ci_mypy.sh` |
| MCP 运维 | v0.12 | ✅ | ✅ | — |

---

## 九、未实现 backlog（建议下一迭代）

按优先级：

1. **`test_llm_circuit_breaker` 集成测试**（Batch 2）
2. **OCR / 多模态反幻觉**（Batch 3.4，按需）
3. **Patroni/pgpool 故障切换集成测试**（Batch 4.4）
6. **真实 Postgres 多租户 schema CI job**（可选 service container）
7. **Graph 压测脚本 + 背压 SLO 文档**
8. **Edge LLM / GDPR / 异地 DR**（Batch 4.5，单独立项）

---

## 十、关键命令

```bash
# 开源冒烟
bash scripts/demo_local.sh

# Golden / Eval CI
SUITE=unit      bash scripts/ci_eval.sh -q
SUITE=integration bash scripts/ci_eval.sh -q
SUITE=rag       bash scripts/ci_eval.sh -q
SUITE=all       bash scripts/ci_eval.sh -q

# 租户存储（需 MULTI_TENANT_ENABLED=true）
bash scripts/create_tenant_storage.sh create acme
bash scripts/create_tenant_storage.sh drop acme
```

---

## 十一、修订记录

| 日期 | 说明 |
|------|------|
| 2026-05-26 | 初版：对齐 Batch 0–4 仓库实现与测试覆盖 |
| 2026-05-26 | 增补 §七 Mission 写作阶段、autonomous、步数预算与 steer |
| 2026-05-26 | 增补 steer 双阶段确认、结构化 `confirm`、session turn 规划闸门与 state_store payload 持久化 |
