# 废弃代码分析

本文档用于记录当前项目中“已废弃、仅兼容保留、或可判定为不再建议继续沿用”的代码路径，并给出移除优先级与落地建议。

## 分析范围

本次重点检查了以下入口与候选模块：

- [`app/main.py`](app/main.py)
- [`app/nodes/mission_decide_node.py`](app/nodes/mission_decide_node.py)
- [`app/services/mission_executor.py`](app/services/mission_executor.py)
- [`app/services/writing_phases.py`](app/services/writing_phases.py)
- [`app/services/legacy_mission_paths.py`](app/services/legacy_mission_paths.py)
- [`app/domain/packs/writing.py`](app/domain/packs/writing.py)
- [`app/services/mission_oma/orchestrator.py`](app/services/mission_oma/orchestrator.py)
- [`app/services/mcp_bridge.py`](app/services/mcp_bridge.py)
- [`app/mcp_stubs/http_server.py`](app/mcp_stubs/http_server.py)
- [`app/mcp_stubs/stdio_server.py`](app/mcp_stubs/stdio_server.py)

同时结合全局检索以下特征进行交叉判断：

- `legacy`
- `deprecated`
- `TODO remove/delete`
- 旧执行路径开关与 manifest
- 仅测试使用的 stub/兼容层

---

## 一、明确属于废弃兼容路径的代码

### 1. 写作任务旧决策链路（legacy writing LLM decide）

#### 证据

- [`should_use_writing_llm_decide()`](app/services/writing_phases.py:124) 的文档字符串明确写着：`Deprecated legacy path`。
- [`mission_decide_node()`](app/nodes/mission_decide_node.py:161) 仍然保留了 `use_writing_llm` 分支。
- [`record_legacy_mission_path("writing_llm_decide")`](app/nodes/mission_decide_node.py:166) 与 [`record_legacy_mission_path("writing_llm_decide")`](app/services/writing_phases.py:143) 持续给该路径打 legacy 指标。
- [`LEGACY_MISSION_PATHS`](app/services/legacy_mission_paths.py:11) 把 `writing_llm_decide` 固定列入旧路径清单。
- [`app/domain/packs/writing.py`](app/domain/packs/writing.py:179) 在写作 pack 规则中仍保留该分支兜底。

#### 判定

这是**明确的废弃兼容代码**，不是主路径。当前项目的写作主流程已经转向 OMA/OMAW，而该逻辑的存在目的主要是：

- 保留旧行为开关；
- 记录命中指标；
- 在少数配置下继续允许旧流程运行。

#### 涉及文件

- [`app/services/writing_phases.py`](app/services/writing_phases.py)
- [`app/nodes/mission_decide_node.py`](app/nodes/mission_decide_node.py)
- [`app/domain/packs/writing.py`](app/domain/packs/writing.py)
- [`app/services/legacy_mission_paths.py`](app/services/legacy_mission_paths.py)
- [`config/legacy_mission_manifest.yaml`](config/legacy_mission_manifest.yaml)
- [`tests/services/test_legacy_mission_paths.py`](tests/services/test_legacy_mission_paths.py)
- [`tests/services/test_legacy_manifest_ci.py`](tests/services/test_legacy_manifest_ci.py)

#### 移除建议

建议列为 **P1：优先移除**。移除顺序：

1. 删除 [`should_use_writing_llm_decide()`](app/services/writing_phases.py:124) 及相关 fallback 分支。
2. 删除 [`mission_decide_node()`](app/nodes/mission_decide_node.py:161) 中 `use_writing_llm` 相关逻辑。
3. 清理 [`app/domain/packs/writing.py`](app/domain/packs/writing.py:179) 中对该旧分支的依赖。
4. 删除 [`app/services/legacy_mission_paths.py`](app/services/legacy_mission_paths.py) 中仅为此路径服务的计数与 manifest 逻辑。
5. 同步删除 legacy 测试与配置项：[`MISSION_ALLOW_LEGACY_WRITING_PATH`](app/config/settings.py:553)、[`MISSION_WRITING_LLM_DECIDE`](app/config/settings.py:512) 的废弃部分说明或整个配置入口。

---

### 2. 写作任务旧 pipeline fallback 路径

#### 证据

- [`run_pipeline_request()`](app/services/mission_executor.py:284) 的注释明确写明：**不得作为手稿 mission_oma 的默认执行器**。
- 当任务类型为 writing 时，会记录 [`record_legacy_mission_path("pipeline_reasoning_writing_fallback")`](app/services/mission_executor.py:301)。
- [`LEGACY_MISSION_PATHS`](app/services/legacy_mission_paths.py:11) 中明确包含 `pipeline_reasoning_writing_fallback`。
- [`_dispatch_oma_act()`](app/services/mission_executor.py:250) 的注释写着：`returns None when caller should use legacy pipeline`，说明该函数仍在兼容旧 pipeline 概念。

#### 判定

这部分代码属于**仍在运行但被架构显式降级为旧路径**的兼容逻辑。对于写作 mission 而言，它已经不是推荐路径。

需要注意：[`run_pipeline_request()`](app/services/mission_executor.py:284) 本身不一定是全局废弃，因为非写作、非 OMA 的场景仍可能使用它；但其中“写作 fallback 到旧 pipeline”的这部分属于废弃路径。

#### 涉及文件

- [`app/services/mission_executor.py`](app/services/mission_executor.py)
- [`app/services/legacy_mission_paths.py`](app/services/legacy_mission_paths.py)
- [`tests/integration/test_mission_oma_golden.py`](tests/integration/test_mission_oma_golden.py)

#### 移除建议

建议列为 **P1：优先移除**，但范围要精确：

- 保留 [`run_pipeline_request()`](app/services/mission_executor.py:284) 对非写作场景的能力；
- 删除写作 mission 命中 legacy fallback 的分支与审计；
- 把写作任务执行统一收敛到 OMA worker / [`run_subgraph_writing()`](app/services/mission_executor.py:449) 主路径；
- 将“返回 `None` 让调用者走 legacy pipeline”的接口语义改成更显式的现代分发语义。

---

### 3. legacy manifest + legacy 路径打点模块

#### 证据

- [`app/services/legacy_mission_paths.py`](app/services/legacy_mission_paths.py) 整个模块的命名与实现都只服务于旧路径追踪。
- [`LEGACY_MISSION_PATHS`](app/services/legacy_mission_paths.py:11) 维护一组 legacy 常量。
- [`load_legacy_manifest()`](app/services/legacy_mission_paths.py:25) 从 [`config/legacy_mission_manifest.yaml`](config/legacy_mission_manifest.yaml) 读取清单。
- 文档 [`docs/ADR_MISSION_LIFECYCLE_V2.md`](docs/ADR_MISSION_LIFECYCLE_V2.md) 与 [`docs/INTENT_OBSERVATION_AND_OMAW_MIGRATION_PLAN.md`](docs/INTENT_OBSERVATION_AND_OMAW_MIGRATION_PLAN.md) 都把它作为迁移期间的封存/审计设施。

#### 判定

该模块本质上不是业务能力，而是**迁移过渡期的观测与兼容设施**。当 legacy 路径彻底删除后，该模块本身也应被删除。

#### 移除建议

建议列为 **P1：跟随旧路径一起移除**：

- 若 `writing_llm_decide` 与 `pipeline_reasoning_writing_fallback` 两类旧路径移除完成，则 [`app/services/legacy_mission_paths.py`](app/services/legacy_mission_paths.py) 可以整体删除；
- 同时清理 metrics 中的 legacy counter 注册与测试断言；
- 删除 [`config/legacy_mission_manifest.yaml`](config/legacy_mission_manifest.yaml) 与对应 CI 校验。

---

## 二、可判定为“仅测试用途”，不属于运行时代码，但不建议误删

### 4. MCP stub 服务

#### 证据

- [`app/mcp_stubs/http_server.py`](app/mcp_stubs/http_server.py) 文件头注释：`for integration tests`。
- [`app/mcp_stubs/stdio_server.py`](app/mcp_stubs/stdio_server.py) 文件头注释：`for local integration tests`。
- 测试 [`tests/integration/test_mcp_http_e2e.py`](tests/integration/test_mcp_http_e2e.py:21)、[`tests/services/test_mcp_bridge.py`](tests/services/test_mcp_bridge.py:19)、[`tests/services/test_mcp_manager.py`](tests/services/test_mcp_manager.py:48) 都直接依赖这些 stub。
- 配置 [`config/config.yaml`](config/config.yaml:608) 里也保留了基于 stub 的 MCP 示例配置。

#### 判定

这些文件**不是废弃代码**，而是**测试夹具/测试桩**。它们不是生产路径，但目前仍被测试与示例配置实际使用。

#### 处理建议

- 不应按“废弃代码”删除；
- 可以在文档中明确标记为“非生产代码”；
- 若后续测试体系改为真实 mock server 或 pytest fixture，可再考虑迁移或收缩目录。

优先级：**P3：保留**。

---

## 三、目前看起来像旧代码，但证据不足，不建议直接删除

### 5. Web 兼容路由 `/cli`

#### 证据

- [`web_cli_redirect()`](app/main.py:169) 的注释写着：`Backward-compatible alias for the conversation page.`
- 当前其行为只是 302 跳转到 [`/chat`](app/main.py:159)。

#### 判定

这是**兼容入口**，但是否“废弃到可以删除”，目前证据不足。因为全局检索没有看到其他 Python 代码再引用它，并不代表外部用户、书签、文档、反向代理配置没有依赖。

#### 处理建议

- 暂不删除；
- 先通过访问日志/网关日志确认 `/cli` 是否仍有流量；
- 若连续一段时间无访问，再移除该 alias。

优先级：**P2：待验证后移除**。

---

### 6. Web 页面入口 `/`、`/chat`、`/dashboard`

#### 判定

虽然这些入口不是在 Python 内部被大量引用，但它们属于 HTTP 暴露路由：

- [`platform_home()`](app/main.py:151)
- [`web_chat()`](app/main.py:160)
- [`monitoring_dashboard()`](app/main.py:177)

这类代码不能用“代码内无调用”来判定废弃，因为它们可能由浏览器直接访问。

#### 处理建议

- 不纳入本次废弃代码删除范围；
- 如果要继续判断，需要结合前端文件、路由文档、访问日志与部署入口一起分析。

优先级：**P3：保留**。

---

## 四、建议删除清单

### 可直接进入移除方案设计的项

1. **写作旧决策链路**
   - [`should_use_writing_llm_decide()`](app/services/writing_phases.py:124)
   - [`build_writing_decide_payload()`](app/services/writing_phases.py:169)（若仅旧 LLM 决策使用）
   - [`mission_decide_node()`](app/nodes/mission_decide_node.py:161) 中 `use_writing_llm` 相关分支
   - [`app/domain/packs/writing.py`](app/domain/packs/writing.py:179) 中 legacy fallback 逻辑

2. **写作旧 pipeline fallback 审计与兼容逻辑**
   - [`record_legacy_mission_path("pipeline_reasoning_writing_fallback")`](app/services/mission_executor.py:301)
   - [`_dispatch_oma_act()`](app/services/mission_executor.py:250) 中“caller should use legacy pipeline”语义

3. **迁移期 legacy 观测设施**
   - [`app/services/legacy_mission_paths.py`](app/services/legacy_mission_paths.py)
   - [`config/legacy_mission_manifest.yaml`](config/legacy_mission_manifest.yaml)
   - legacy metrics / legacy tests / legacy manifest CI

---

## 五、推荐的移除顺序

### Phase 1：先消除旧决策入口

- 去掉 [`mission_decide_node()`](app/nodes/mission_decide_node.py:161) 中 `use_writing_llm` 分支；
- 去掉 [`app/domain/packs/writing.py`](app/domain/packs/writing.py:182) 中基于 `should_use_writing_llm_decide` 的 fallback；
- 让写作任务统一走 OMA/规则决策。

### Phase 2：清理 legacy 审计设施

- 删除 [`app/services/legacy_mission_paths.py`](app/services/legacy_mission_paths.py)；
- 删除 [`config/legacy_mission_manifest.yaml`](config/legacy_mission_manifest.yaml)；
- 清理相关测试、指标、文档引用。

### Phase 3：收口 executor 中的旧 pipeline 语义

- 重构 [`run_pipeline_request()`](app/services/mission_executor.py:284) 与 [`_dispatch_oma_act()`](app/services/mission_executor.py:250) 的注释和分支；
- 明确只保留当前仍有效的非写作或非 OMA 执行语义；
- 删除“写作 fallback 到 legacy pipeline”的残余记录。

---

## 六、结论

本项目当前最明确的废弃代码，不是普通“未被 import 的死代码”，而是**已经被架构升级替代、但仍为兼容与迁移观测而保留的 legacy 路径**。其中最值得优先清理的是：

- [`should_use_writing_llm_decide()`](app/services/writing_phases.py:124) 及其调用链；
- [`run_pipeline_request()`](app/services/mission_executor.py:284) 中写作任务的 legacy fallback 语义；
- [`app/services/legacy_mission_paths.py`](app/services/legacy_mission_paths.py) 与 [`config/legacy_mission_manifest.yaml`](config/legacy_mission_manifest.yaml) 这套迁移期观测设施。

而 [`app/mcp_stubs/http_server.py`](app/mcp_stubs/http_server.py)、[`app/mcp_stubs/stdio_server.py`](app/mcp_stubs/stdio_server.py) 这类代码虽然不是生产能力，但仍被测试真实使用，**不应误删**。

综合建议：

- **立即进入移除设计**：legacy writing decide / legacy writing pipeline fallback / legacy manifest-audit。
- **先观测再决定**：[`/cli`](app/main.py:168) 兼容路由。
- **保持不动**：测试 stub、HTTP 页面路由。
