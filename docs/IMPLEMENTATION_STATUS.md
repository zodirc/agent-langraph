# 实现状态追踪（对齐代码库）

> **最后更新**：2026-06-05（Evidence Operating System v2 全链路 + intent observation；见 [`RETRIEVAL_OPTIMIZATION_EXECUTION_PLAN.md`](RETRIEVAL_OPTIMIZATION_EXECUTION_PLAN.md)、[`INTENT_OBSERVATION_AND_OMAW_MIGRATION_PLAN.md`](INTENT_OBSERVATION_AND_OMAW_MIGRATION_PLAN.md)）  
> **对照文档**：[`CAPABILITY_MATRIX.md`](CAPABILITY_MATRIX.md)  
> **图例**：✅ 已实现并有用例覆盖 · 🔶 部分实现 / 默认关 · ❌ 未实现

当前代码库处于 **v0.11–v0.13 能力已落地、v0.13 平台化持续补齐** 状态（以仓库实测为准，非发布 tag）。

---

## 一、批次总览

| Batch | 主题 | 状态 | 说明 |
|-------|------|------|------|
| **0** | 开源可用性 | ✅ **完成** | README 5 分钟路径、`scripts/demo_local.sh`、`CAPABILITY_MATRIX.md` |
| **1** | 可信问答 | ✅ **核心完成** | RAG 闭环 + Golden 26 项；语义压缩逻辑到位，E2E/指标门禁仍未齐 |
| **2** | 稳定性 | ✅ **核心完成** | 背压、熔断、Checkpoint 恢复 |
| **3** | 能力模块化 | ✅ **核心完成** | Skill、MCP 运维、Embedding 治理；OCR 未做 |
| **4** | 平台化 | 🔶 **核心完成** | 租户隔离/配额/成本/API；HA、长期合规项未做 |
| **5** | Runtime 认知控制增强 | ✅ **完成** | route audit、reasoning isolation、SRDL、turn event log、agenda / DAG、confirmation gates |

---

## 二、Batch 0 — 开源可用性

| ID | 任务 | 状态 | 产物 |
|----|------|------|------|
| B0-1 | 5 分钟快速路径 | ✅ | `README.md` 顶部 quick start / Docker / CLI |
| B0-2 | `demo_local.sh` | ✅ | `scripts/demo_local.sh` |
| B0-3 | 能力矩阵 | ✅ | [`docs/CAPABILITY_MATRIX.md`](CAPABILITY_MATRIX.md) |
| B0-4 | 受控文件编辑文档 | ✅ | `artifact_tools.py` / `builtin_tools.py` + matrix / README 对齐 |

---

## 三、Batch 1 — 可信问答

### 1.1 会话上下文压缩与多轮记忆

| 项 | 状态 | 说明 |
|----|------|------|
| `context_compressor.py` | ✅ | `SemanticContextSummary`、字符 / 语义双路径；当前定位为 transcript 压缩与 `semantic_summary` 生成子能力 |
| **Context Governance（ADR §13 DoD）** | ✅ | 网关 + policy + assembler + registry + API + DoD eval；**Web** `/chat` 内嵌上下文治理面板（§1.1 #6）；见 [`CONTEXT_GOVERNANCE.md`](CONTEXT_GOVERNANCE.md) |
| `prompt_context_gateway` 统一入口 | ✅ | planning / reasoning / writing / reviewing / reflection / routing / code_agent / session_turn 统一经治理组包 |
| `conversation_context` 模块 | ✅ | transcript 生命周期；治理启用时不再作为直接 prompt 主路径 |
| `session_turn` 接入 | ✅ | `prepare_session_turn`；压缩见 `compress_session_history()` |
| session turn 策略闸门 | ✅ | `SESSION_TURN_POLICY.md` 对齐：mission active 时的 intent classifier、机械续写与 planning gate |
| 配置 `session.memory_retrieval_enabled` | ✅ | 多轮 QA 在 `skip_retrieval` 时仍走 session memory |
| 配置 `context_compress.semantic_enabled` | ✅ | 默认 **false** |
| 单测 | ✅ | `tests/services/test_context_compressor.py` |
| E2E | 🔶 | 代码库已有 integration 覆盖迹象，但门禁口径仍未完全在本文档闭环 |
| CI 压缩率门禁 | ❌ | `agent_context_compress_ratio` 未形成硬 gate |
| DoD（开源可用性批次） | 🔶 | 逻辑完整；E2E/门禁仍可补强 |

### 1.2 RAG 精排 + Citation + Faithfulness

| 项 | 状态 | 说明 |
|----|------|------|
| `reranker.py` | ✅ | lexical / cross_encoder / cohere |
| `rag_eval.py` | ✅ | citation、启发式 + 可选 LLM faithfulness |
| `rag_metrics.py` | ✅ | Recall@k、MRR、NDCG |
| 节点接入 | ✅ | retrieval / output / output_guard / fact_layer |
| 配置 `rag.*` | ✅ | rerank、citation、faithfulness 默认关 |
| 单测 | ✅ | `test_reranker.py`、`test_rag_eval.py` |
| Eval | ✅ | `test_rag_golden.py`、`test_rag_faithfulness_llm.py` |
| CI | ✅ | `scripts/ci_eval.sh` SUITE=rag；阈值在 `eval_thresholds.py` |
| Faithfulness 生产 LLM 实测 | 🔶 | CI 仅 mock；live 依赖 API Key |

### 1.3 Golden 评测集

| 层级 | 目标 | 实际 | 状态 |
|------|------|------|------|
| Unit | 8+ | 8 | ✅ `tests/eval/baseline.json` |
| Integration | 8+ | **10** | ✅ `integration_cases.json` + `integration_baseline.json` |
| RAG | 8+ | **8** | ✅ `rag_golden_tasks.yaml` + `rag_baseline.json` |
| 合计 | 20+ | **26** | ✅ |
| `test_golden_quality` | — | ✅ | doc_id 存在性校验 |
| `ci_eval.sh` | unit/integration/rag/all | ✅ | |
| Mission planning handoff | — | ✅ | `enable_planning_mission_handoff` + sync stream handoff |
| 通过率门禁 85%/80% | — | 🔶 | baseline 回归有；聚合通过率仍可加强 |

### 1.4 Evidence Operating System（Retrieval v2）

| 项 | 状态 | 说明 |
|----|------|------|
| 八层 Evidence OS 架构 | ✅ | `evidence_pipeline.py` 包装 hybrid search；见执行方案 §5.1 |
| Task Intent + Query Construction | ✅ | `retrieval_decision.py`、`query_builder.py` → `RetrievalDecision` / `QueryObject` |
| Multi-Retrieval 增强 | ✅ | lexical RRF 加权、stale 预过滤、recency score（`retrieval_search_policy.py`） |
| Admission Gate + Snippet-first | ✅ | `evidence_assembly.py`、`snippet_extractor.py`；soft gate + shadow hard gate |
| 统一证据层级 | ✅ | `evidence_hierarchy.py`；user/tool/memory/kb 分层注入 gateway |
| Grounding + Citation 校验 | ✅ | `grounding_check.py`、`insufficient_evidence.py`；`output_guard_node` 接入 |
| 失败归因 + 可观测 | ✅ | `failure_attribution.py`、`retrieval_observability.py`；`retrieval_trace` 全链路 |
| 节点接入 | ✅ | `retrieval_node` / `planning_node` / `writing_node` / `reasoning_node` / `output_guard_node` |
| 配置 `retrieval.*` | ✅ | `config.yaml` → `enable_evidence_pipeline`、`unified_hierarchy`、`purpose_thresholds` 等 |
| 单测 + E2E + Replay | ✅ | 135 项 pytest 全绿；`run_evidence_replay.py` 10 条用例 recall_proxy=1.0 |
| 生产灰度 A/B | 🔶 | 默认 soft gate + shadow；Grafana 面板待建 |
| runtime_router 直连接入 | 🔶 | 决策经 `retrieval_routing.py` 从 planning/writing 挂载 |

---

## 四、Batch 2 — 稳定性

| 项 | 状态 | 关键文件 / 测试 |
|----|------|-----------------|
| 图执行背压 | ✅ | `graph_execution_pool.py`；429 `GraphExecutionRejected`；`test_graph_runner_backpressure.py` |
| Prometheus 队列指标 | ✅ | `graph_queue_active` / `graph_rejected_total` 等 |
| LLM Circuit Breaker | ✅ | `circuit_breaker.py`；`llm.circuit_breaker_enabled`；`test_circuit_breaker.py` |
| LLM 集成熔断 E2E | 🔶 | 单测有；端到端门禁仍可继续补 |
| Checkpoint 恢复 | ✅ | `checkpoint_recovery.py`；`test_checkpoint_recovery.py` |
| 压测脚本 / SLO 文档 | ❌ | 文档级仍待补 |

---

## 五、Batch 3 — 能力模块化

| 项 | 状态 | 说明 |
|----|------|------|
| Skill 运行时（legacy invoke） | ✅ | `skill.py`、`skill_registry.py`、`config/skills/`；`test_skill_registry*` |
| **Skill Platform Phase 1** | ✅ | 目录 API、`skill_id` 任务注入、overlay 模板化；Web 目录 + CLI 动态 `input_form_schema` 表单 |
| **Skill Platform Phase 2** | ✅ | 租户 YAML 存储、CRUD/发布/停用/归档/克隆/回滚；`GET /skills/catalog/tenant`；管理台 output contract / examples / tags |
| **Skill Platform Phase 3** | ✅ | 分阶段 hooks（pre_task / tool_filter / candidate_score / output_validate）、dry-run、按 skill 工具指标 |
| **Skill Platform Phase 4** | ✅ | 受信任 hooks 详情 API、enterprise 包、治理与 marketplace 预览；Supervisor 任务支持 `skill_id` |
| **§13 预制 Skills（16）** | ✅ | `config/skills/` 全部 16 项 + `input_form_schema`；单测 `test_skill_builtin_catalog.py` |
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

### 4.1 多租户 / 鉴权 / 计量（2026-06 加固）

| 项 | 状态 | 说明 |
|----|------|------|
| JWT / API Key 携带 `tenant_id` | ✅ | `auth_service.py`；登录响应返回租户 |
| 请求头租户与 principal 一致性校验 | ✅ | `tenant_access.py` + `deps.get_current_principal` |
| 任务对象级授权（user_id） | ✅ | `require_task_access_dep` on `/tasks/{id}/*` |
| 租户配额 Redis 后端 | ✅ | `tenant.quota_backend: redis`；不可用时回退 memory |
| LLM `logical` vs `billed` 指标 | ✅ | `llm_client._record_llm_usage`；配额/成本仅计 `billed` |
| 缓存命中不计配额/成本 | ✅ | `invoke_structured` cached 分支 `bill_quota=false` |
| 生产环境禁止 `AUTH_ENABLED=false` | ✅ | `startup_checks.validate_runtime_security` |

**适用边界（运维必读）**：

- **SQLite 按租户分库**：适合开发 / 轻量生产，不适合大规模 SaaS。
- **`AUTH_ENABLED=false`**：仅 `APP_ENV=development`（或关闭 `auth.require_in_production`）。
- **内存配额**：单进程可用；多副本请设 `tenant.quota_backend: redis`。

**Docker 部署**（`CONFIG_PATH=config/config.docker.yaml`）：

| 文件 | 作用 |
|------|------|
| [`config/config.docker.yaml`](../config/config.docker.yaml) | 容器内主配置；DB 主机 `postgres`，Redis 主机 `redis` |
| [`docker-compose.yml`](../docker-compose.yml) | 注入 `CONFIG_PATH`、`DATABASE_URL`、`AUTH_*`、`TENANT_*` |
| [`docker-compose.dev.yml`](../docker-compose.dev.yml) | `APP_ENV=development`，挂载 `./config` |
| [`docker-compose.redis.yml`](../docker-compose.redis.yml) | 可选 Redis + 分布式配额 / 限流 |

生产 `docker compose up` 默认 `AUTH_ENABLED=true`；开发叠加 `docker-compose.dev.yml` 可 `AUTH_ENABLED=false`。

---

## 七、Batch 5 — Runtime 认知控制增强（2026-05 末至 2026-06）

### 5.1 路径审计与多轮推理隔离

| 项 | 状态 | 说明 |
|----|------|------|
| Route audit（规划后） | ✅ | `app/services/route_audit/`；规划完成后审计任务类型 vs 执行路径 |
| Pre-planning（先模式后规划） | ✅ | `pre_planning.py`；`interaction_mode`；`engineering_thin_skip` 跳过 planning LLM |
| Web `/chat` 交互模式切换 | ✅ | 顶栏 + `/mode` → `input_payload.interaction_mode`；`web/static/app.js` |
| Planning payload 健壮性 | ✅ | `manuscript_service._coerce_dict`；工程文件名不再误走 text-artifact 扩展名校验 |
| Intent → Mode 路由（工程沙箱） | ✅ | `mode_router` / `mode_registry` / `mode_resolution`；见 [`ENGINEERING_AGENT_SANDBOX_PROPOSAL.md`](ENGINEERING_AGENT_SANDBOX_PROPOSAL.md) |
| `engineering_mode` + `engineering_bounded` | ✅ | `engineering_execution` 节点；`project_verify`：`cpp` / `python` / `web_html_js` / `make_cpp_demo` |
| `code_artifact` 推理侧编译（路径 A） | ✅ | 临时 `code_verify` workspace；与工程落盘路径分离，见 [`CODE_AND_ENGINEERING_PATHS.md`](CODE_AND_ENGINEERING_PATHS.md) |
| 代码双路径 / 模式切换说明文档 | ✅ | [`CODE_AND_ENGINEERING_PATHS.md`](CODE_AND_ENGINEERING_PATHS.md) |
| 会话文件工具说明文档 | ✅ | [`SESSION_FILE_TOOLS.md`](SESSION_FILE_TOOLS.md)；grep/replace/rm 两阶段等 |
| `verify_backend` 工具 | ✅ | `verify_backend_tool.py`；白名单 backend，无任意 shell |
| 三模式 contract 驱动执行 | ✅ | `mode_execution.py`：`qa_mode` / `manuscript_mode` / `engineering_mode` 均落 contract |
| Golden eval 工程场景 | ✅ | `integration_cases.json`：2048 / cpp / Makefile / session 切换 / qa 阻断 |
| Live LLM E2E（可选 CI） | ✅ | `tests/e2e/test_engineering_mode_live.py`；`ENGINEERING_E2E_LIVE=1`；`ci_eval.sh SUITE=engineering` |
| Reflection 路由纠错 + replan | ✅ | `retry_planning`；`reflection.route_audit_on_misroute` |
| Artifact profile | ✅ | `source_code` / `manuscript_prose` / `outline` |
| 多轮 reasoning 隔离 | ✅ | `reasoning_shortcut.py`；每轮清零，问答强制完整思考 |
| Segment-aware output_guard | ✅ | `content_segments.py` + `output_guard.pii_prose_only` |
| Session memory query 增强 | ✅ | `memory_query.py`；`session_memory_*` counters |

### 5.2 长篇 Mission 编排增强

| 项 | 状态 | 说明 |
|----|------|------|
| 手稿子系统 | ✅ | `manuscript_service`、`writing_node`、`build_writing_context` |
| 写作流式 WGC (v1) | ✅ | `artifact_args_parser`、`writing_generation`、断流 partial + transport 重试；见 `docs/contracts/WRITING_GENERATION_CONTRACT.md` |
| Planning → Mission handoff | ✅ | `enable_planning_mission_handoff` + `mission_auto` |
| Lazy work_plan | ✅ | `mission_orchestrator` 按 `step_policy` 推进一步 |
| agenda / DAG 扩展 | ✅ | `task_agenda.py`：`depends_on`、阻塞传播、局部重规划；支持 batch unit agenda 投影 |
| Mission OMAW（ADR-001） | ✅ | M1–M8 闭环；`SUITE=oma` CI；§8.1 全链 golden；`chapter_facts`→KnowledgeStore；`run_pipeline_request` OMAW 重定向；12 章并行 subtasks 单测 |
| `FactBundle` / `ReviewVerdict` 领域模型 | ✅ | 新增 capability-aware 事实包与统一章节验收语义；review verdict 强制绑定 `fact_bundle_id` |
| turn kind / planning→executor 路由 | ✅ | `turn_kind.py` 区分 `steer_replan` / `steer_execute` / `mission_step_execute` / `mechanical_continue`；禁止执行回合误以 reasoning 收尾 |
| **Intent observation model（L0–L3）** | ✅ | `intent_observation.py` + `intent_observation_policy.py`；`pre_planning` 消费结构化结果；`purpose=intent_observation` 上下文治理；Prometheus `agent_intent_observation_*` |
| 写作阶段（模型自选） | ✅ **已退役** | 默认 hard-off；兼容层见 `allow_legacy_writing_path`；`IMPLEMENTATION_STATUS` 跟踪见迁移方案 §12 |
| autonomous 连续执行 | ✅ | `init_mission_state` 保留 autonomous；`stepwise_pause` 对 autonomous 为 false |
| 输出保留 `MISSION_PAUSED` | ✅ | `output_node._resolve_output_status` |
| Streaming 深合并修复 | ✅ | `merge_state` 深合并 `progress/input_payload` |
| 纲文对齐决策 | ✅ | `outline_body_alignment.py` + `writing_node` 强制 `reset_body` |
| `budget.max_steps` 估算 + 硬顶 500 | ✅ | `mission_schema.resolve_mission_budget_dict` |
| Supervisor 按章多 Agent | ❌ | ADR 禁止用于手稿 mission；`manuscript_supervisor_guard` |

### 5.3 Steer / confirmation / execution control

| 项 | 状态 | 说明 |
|----|------|------|
| Steer intent 确认（规划后、执行前） | ✅ | `mission_steer_confirm.py`；`planning_node` |
| Steer outcome 确认（工作项后、节选） | ✅ | `confirmation/` 预览策略 + `mission_steer_outcome_confirm.py` |
| `confirmation_gates` 配置化 | ✅ | `confirmation/config.py` + `gate_registry.py` + `preview_resolver.py` |
| `work_plan_patch` | ✅ | `planning_node` + `mission/steer_replan.py` |
| 结构化批准 `confirm:true` | ✅ | `resume` / `steer` API；`steer_confirmation_actions.py` |
| Steer payload 持久化 | ✅ | `state_store._PAYLOAD_VOLATILE_KEYS` |
| Steer 优先级 / 抢占提示 | ✅ | `POST /tasks/{id}/steer` 支持 `priority`/`preempt` |
| Session turn 规划闸门 | ✅ | `session_turn.py`；`graph_runner._prepare_mission_for_turn` |
| execution_grant | ✅ | `mission_execution.py`；`resume_api` / session 机械续写签发继续令牌 |
| pause_reason 分型 | ✅ | `step_checkpoint` / `gate_intent` / `gate_outcome` / `worker_lost` / `budget` / `failure` 等 |
| executor registry / orphan running reconcile | ✅ | `graph_run_registry.py` 跟踪活跃执行器；`mission_worker_lost.py` 把孤儿 `MISSION_RUNNING` 收敛为 `MISSION_PAUSED(worker_lost)` |
| client_display | ✅ | `system_lines` / `autonomous_ui` / `client_display` |

### 5.4 事件账本与事实层增强

| 项 | 状态 | 说明 |
|----|------|------|
| `turn_event_log` | ✅ | `TurnEventLog` 结构化记录 decision / execution / quality 事件 |
| `turn_facts` | ✅ | `fact_layer` 从事件账本与状态汇总本轮事实 |
| 代码 artifact 校验事件 | ✅ | `code_artifact_pipeline.py` 写入 verify / repair 事件 |
| reasoning 修复可观测性 | ✅ | audit + metrics：`reasoning_parser_*` |
| route / contract / react 审计 | ✅ | `route_audit`、`turn_contract`、`react_audit` 均归档到事件账本 |

### 5.5 SRDL（受控 ReAct / 自路由审议回路）

| 项 | 状态 | 说明 |
|----|------|------|
| 状态 `react_loop` / trace | ✅ | `app/domain/react_loop.py`，`AgentState.react_loop` |
| 入口判定 | ✅ | `should_enter_react_loop`（启发式 + `force_react_loop` / `disable_react_loop`） |
| Single 子图 | ✅ | `react_deliberate` → `execute` → `observe` → `finalize` → `reasoning` |
| 动作白名单 | ✅ | retrieve / memory / tool / reason / replan / finish |
| 审计事件 | ✅ | `react_audit.py` + `turn_event_log` |
| 制度升级建议 | ✅ | `route_recommendation`（系统裁决，非 mid-loop 自由切图） |
| 配置 | ✅ | `config.yaml` → `react_loop.*`（默认开启） |
| Prometheus | ✅ | `agent_react_*` counters/histogram（`finalize_loop` 导出） |
| 单测 / 集成 | ✅ | `test_react_entry.py`、`test_react_loop_runner.py`、`test_react_graph_flow.py` |

启用：默认 `react_loop.enabled: true`；可关闭或对单次请求设 `input_payload.disable_react_loop: true` / `force_react_loop: true`。

---

## 八、验收总表

| 缺口项 | 计划版本 | 实现 | 测试 | CI 门禁 |
|--------|----------|------|------|---------|
| 语义上下文压缩 | v0.11 | ✅ | 🔶 | ❌ ratio hard gate |
| RAG 精排+citation+faithfulness | v0.11 | ✅ | ✅ | ✅ rag suite |
| Evidence Operating System v2 | v0.13- | ✅ | ✅ 135 项 | 🔶 replay 有；Grafana 待建 |
| Golden 20+ | v0.11 | ✅ 26 项 | ✅ | ✅ baseline 回归 |
| 背压队列 | v0.11.1 | ✅ | ✅ | 🔶 压测报告未成文 |
| Circuit breaker | v0.11.1 | ✅ | ✅ 单测 | 🔶 集成 E2E 可补 |
| Checkpoint 恢复 | v0.11.1 | ✅ | ✅ | — |
| Skill 运行时 | v0.12 | ✅ | ✅ | — |
| Embedding 治理 | v0.12 | ✅ | ✅ | — |
| OCR 反幻觉 | v0.12 | ❌ | ❌ | ❌ |
| 多租户 | v0.13 | ✅ | ✅ quota+isolation | — |
| AgentState Pydantic | v0.13 | ✅ 图边界 | ✅ model | ✅ `ci_mypy.sh` |
| MCP 运维 | v0.12 | ✅ | ✅ http e2e | — |
| Route audit / reasoning isolation | v0.12+ | ✅ | ✅ | — |
| confirmation gates / execution grant | v0.12+ | ✅ | ✅ | — |
| SRDL | v0.13- | ✅ | ✅ | — |
| Session 文件工具集（会话目录隔离） | v0.13- | ✅ | ✅ | — · [`SESSION_FILE_TOOLS.md`](SESSION_FILE_TOOLS.md) |
| 代码双路径 + 模式切换文档 | v0.13- | ✅ | — | — · [`CODE_AND_ENGINEERING_PATHS.md`](CODE_AND_ENGINEERING_PATHS.md) |
| Pre-planning + Web 模式 + 工程 thin plan | v0.13- | ✅ | ✅ | `test_pre_planning.py`、`test_planning_engineering_thin.py` |
| Mission 工具白名单扩展（writing pack） | v0.13- | ✅ | ✅ | — |
| Mission 自动工具注入开关（默认关） | v0.13- | ✅ | ✅ | — |
| Web 会话文件侧栏 + 实时预览编辑 | v0.13- | ✅ | ✅ API | — |

---

## 九、未实现 backlog（建议下一迭代）

按优先级：

1. **语义压缩 E2E + 压缩率门禁**
2. **LLM circuit breaker 集成测试**
3. **Graph 压测脚本 + 背压 SLO 文档**
4. **OCR / 多模态反幻觉**
5. **Patroni/pgpool 故障切换集成测试**
6. **真实 Postgres 多租户 schema CI job**
7. **Edge LLM / GDPR / 异地 DR**
8. **Web UI 现代化** — 方案见 [`WEB_UI_MODERNIZATION.md`](WEB_UI_MODERNIZATION.md)（React/Vite SPA、`/cli` legacy、多阶段 Docker）；当前仍为 `web/static` 终端 CLI

---

## 十、关键命令

```bash
# 开源冒烟
bash scripts/demo_local.sh

# Golden / Eval CI
SUITE=unit        bash scripts/ci_eval.sh -q
SUITE=integration bash scripts/ci_eval.sh -q
SUITE=oma         bash scripts/ci_eval.sh -q   # ADR-001 M8 golden
SUITE=engineering bash scripts/ci_eval.sh -q # 2048/cpp/Makefile/模式切换 golden + 工程单测
SUITE=rag         bash scripts/ci_eval.sh -q
SUITE=all         bash scripts/ci_eval.sh -q

# 工程 Live E2E（需真实 LLM，默认 CI 跳过）
ENGINEERING_E2E_LIVE=1 pytest tests/e2e/test_engineering_mode_live.py -v

# 租户存储（需 MULTI_TENANT_ENABLED=true）
bash scripts/create_tenant_storage.sh create acme
bash scripts/create_tenant_storage.sh drop acme

# SRDL / 路由 / 事件账本相关测试
pytest tests/services/test_react_entry.py tests/services/test_react_loop_runner.py tests/integration/test_react_graph_flow.py -q
```

---

## 十一、修订记录

| 日期 | 说明 |
|------|------|
| 2026-06-04 | 落地 Cursor 式交互：`pre_planning`（planning 前 `mode_resolution`）、`engineering_thin_skip`、显式/API/Web `interaction_mode`；`/chat` 顶栏四档模式 + `/mode`；修复 planning 对非 dict `manuscript`/`mission` 与工程扩展名误杀；文档与能力矩阵、验收表、单测同步。 |
| 2026-06-04 | 文档补齐代码/工程能力：新增 [`CODE_AND_ENGINEERING_PATHS.md`](CODE_AND_ENGINEERING_PATHS.md)（`code_artifact` vs `engineering_mode`、模式 `stay/switch/isolate`、`project_verify` 四类 backend）；新增 [`SESSION_FILE_TOOLS.md`](SESSION_FILE_TOOLS.md)；README 子系统表、能力矩阵、§5.1/§十命令与 `CODE_ARTIFACT_PIPELINE` / `ROUTE_AUDIT` 交叉引用同步。 |
| 2026-06-04 | 对齐最近提交：① Context Governance 正式收敛为统一长期上下文架构，新增 `prompt_context_gateway` / `context_policy` / `context_assembler` / composition trace 与 `/chat` 上下文治理面板；② 文档同步明确字符压缩与 `context_compressor` 已退居 transcript / semantic summary 子能力，不再作为 prompt 主路径；③ README、能力矩阵、实现状态、Mission 文档统一补齐“执行治理 + 上下文治理”双主线。 |
| 2026-06-03 | 对齐最近 3 次提交：① Mission OMAW 落实为默认长文执行路径，新增 `FactBundle` / `ReviewVerdict` / `WorkerExecutionPolicy` / `turn_kind`；② Mission control 增补 `worker_lost` pause、`graph_run_registry` 活跃执行器跟踪，SSE 刷新不再清 live；③ 文档补齐 `ADR_MISSION_LIFECYCLE_V2.md`、`MISSION_EXECUTION_CONTROL.md`、`MANUSCRIPT_WRITING.md` 的 OMAW / executor 控制语义。 |
| 2026-06-02 | 检索链路优化：`knowledge.top_k` 提升到 8、默认启用 lexical rerank、关键词检索升级为 BM25 + CJK token（含中文 bigram）、`fetch_k_multiplier` 扩候选池、向量检索前置 tenant/domain 过滤、`max_chunks_per_doc` 去重；文档见 `docs/RETRIEVAL_OPTIMIZATION.md`。 |
| 2026-06-02 | 近 3 次提交增量对齐：① mission/tools 会话文件工具集（grep/replace/touch/mkdir/ls/read/write/append/move/copy/rm 两阶段 dry_run token）；② Writing Pack 工具白名单扩展 + mission 工具型 work item（`patch_recent_chapter`/`consistency_check`）可选自动注入（`auto_tool_injection` 默认 false，`work_item.params.auto_tools` 可覆盖）；③ Web CLI 增加会话文件侧栏、目录导航/面包屑、双击文件实时预览与保存编辑接口。 |
| 2026-06-02 | 新增 Web UI 现代化方案文档 `WEB_UI_MODERNIZATION.md`（待实施） |
| 2026-06-01 | 对齐近期代码：SRDL、turn_event_log、agenda / DAG、confirmation gates、execution grant、route audit、reasoning isolation |
| 2026-05-26 | 初版：对齐 Batch 0–4 仓库实现与测试覆盖 |
| 2026-05-26 | 增补 Mission 写作阶段、autonomous、步数预算与 steer |
| 2026-05-26 | 增补 steer 双阶段确认、结构化 `confirm`、session turn 规划闸门与 state_store payload 持久化 |
