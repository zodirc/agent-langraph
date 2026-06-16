# 系统优化与演进建议

> 基于当前项目 `Agent LangGraph Runtime` 与外部参考文档 `other_system.md` 的对照分析。
> 本文面向架构演进、工程治理和后续迭代排期，不替代 `docs/arch.md` 与 `docs/rag_skills.md` 的现状说明。

## 1. 基准判断

当前项目不是简单聊天机器人，而是一个以 **Turn 闭环** 为核心的多模式 Agent Runtime。它已经具备较完整的运行时控制面：事件分类、前台首响、中断控制、增量规划、检索、工具执行、上下文治理、推理/写作、验证、策略、人工审核、输出与异步沉淀。

参考系统 `other_system.md` 更强调企业级 Agent 产品能力：IDE / CLI / Web 多端现场工具、Skill / MCP / CLI 资产一键接入、Auto 模型路由、多智能体 fan-out、工作流脚本、定时自治、结构化提问、严格工具契约和验证型交付。

因此，本项目的优化重点不应是重写主链路，而应是：

- 保留当前 **状态治理、证据治理、上下文治理、产物诚实性** 等优势。
- 补齐 **企业资产接入、工程现场验证、多智能体编排、模型路由、调度自治、前端交互契约** 等产品化能力。
- 将“能跑通”升级为“可运营、可评测、可灰度、可恢复、可审计”。

## 2. 设计理念

### 2.1 从对话系统转向任务操作系统

当前架构已经把用户输入理解为一次 Turn，而不是一次无状态消息。后续优化应继续沿着“任务操作系统”的方向演进：

- **输入是事件**：每条用户输入都应被归入明确事件语义，例如新任务、补充约束、恢复、重试、状态查询。
- **计划是控制面产物**：模型生成的计划不是说明文字，而是驱动路由、工具、验证和输出契约的结构化中间产物。
- **工具是受治理的动作面**：任何写入、外发、删除、运行命令都必须带边界、审计、失败语义和回合契约。
- **输出是投影**：前端不应猜测任务状态，而应渲染后端投影的阶段、动作、验证结果和最终交付。

### 2.2 用证据和回执约束模型自由度

模型擅长理解与生成，但不应直接成为事实来源。系统应持续强化以下原则：

- 检索结果、工具结果、文件切片、验证报告都应作为 `ContextItem` 或结构化 trace 进入上下文治理。
- 任何“已完成”“已写入”“已验证”的表述必须能追溯到工具结果或验证回执。
- 规划、执行、验证、输出之间应通过结构化状态衔接，减少自然语言二次解析。
- 压缩和裁剪不能只追求短，而要保留 anchor、来源、冲突和保真率。

### 2.3 优先演进控制点，而非堆叠能力点

参考系统的能力清单很完整，但直接堆功能会增加路由复杂度。建议优先优化控制点：

- 工具循环如何收敛。
- 何时必须验证。
- 哪些任务需要澄清。
- 哪些资产可被自动加载。
- 哪些模型适合哪类 purpose。
- 多智能体结果如何合并和验收。

控制点稳定后，再扩展 Skill、MCP、IDE、调度和工作流能力，系统复杂度才可控。

## 3. 当前优势

| 领域 | 当前优势 | 应保留的工程原则 |
|---|---|---|
| Turn 控制 | `Session` / `Run` / `Turn` 概念清晰，支持中断、恢复、取消、状态投影 | 所有入口统一进入控制面，不绕过 Turn |
| 主图骨架 | `event_classification -> acknowledge -> interrupt_control -> incremental_planning -> ... -> output` frozen spine 明确 | 新能力优先作为节点、路由或受控 Action 接入 |
| 检索证据 | 混合召回、证据管线、冲突检测、token 预算、失败归因较完整 | 检索结果必须可追踪、可过滤、可评价 |
| 上下文治理 | 七桶预算、purpose 策略、compression receipt、evidence fidelity 已形成体系 | 上下文入口统一走 gateway，不在节点内私塞材料 |
| 写作产物 | 写作项目、素材卡、大纲、批量续写、产物 honesty 机制成熟 | 产物状态必须由工具和 artifact 元数据确认 |
| 评测基础 | `tests/eval`、golden、replay、RAG DoD、integration case 已具备 | 每个高风险能力都应有 golden 与阈值门禁 |

## 4. 主要差距

| 参考系统能力 | 当前项目状态 | 差距判断 |
|---|---|---|
| 企业资产一键接入 | Skills YAML 与 MCP bridge 已有，MCP 默认关闭，接入模板不足 | 能力存在，但产品化与运维化不足 |
| IDE / CLI 现场能力 | 主要是 Web/API 与 artifact/code pipeline | 对真实仓库级改动、worktree 隔离、运行观察支持不足 |
| Auto 模型路由 | 有 model catalog、purpose token/timeout，但没有完整路由策略面 | 缺少模型选择决策、成本治理和评测闭环 |
| 多智能体 fan-out | Supervisor / worker graph 存在，但更偏骨架 | 缺少通用 workflow 原语、结构化 fan-in、对抗校验 |
| 验证型交付 | `project_verify` 与 code artifact verify 已有 | 验证尚未成为所有工程任务的一等 Action |
| 结构化澄清 | planning clarification / human review 存在 | 前端事件和选择题契约不够突出 |
| 调度自治 | scheduler / schedule API 存在 | 与 Turn、状态、通知、失败恢复的关系需明确 |
| 文档与配置 | `config.yaml` feature flag 丰富 | 缺少生产 profile、灰度策略和配置解释文档 |

## 5. 优先级路线图

### P0：收敛与验证基线

目标是在不扩展大功能的前提下，先提升主链稳定性和可回归性。

建议事项：

- 统一工具循环收敛门控，减少 legacy guard 与 `evaluate_convergence` 并存带来的行为分叉。
- 将 verification 结果标准化为可消费对象，输出必须引用验证状态。
- 为 `planning_gate_router`、`context_governance_node`、`engineering_node`、`reasoning_or_writing_node` 增加节点级测试。
- 为 `scheduler_service` 增加 service 级测试，覆盖 cron、一次性任务、重复触发、任务过期、失败重试。
- 补齐 `artifacts_api`、`sessions_api`、`a2a_api`、`feedback_api` 等管理面 API 测试。

工程实现原则：

- 先改“判定点”，再改“动作点”。
- 每个路由分支都必须有测试样例，不依赖人工阅读 trace 判断。
- 收敛失败必须进入可解释状态，例如 `dead_letter`、`needs_clarification`、`verification_failed`，不能沉默地继续生成。

### P1：资产接入与工程现场能力

目标是让系统更接近参考系统描述的企业研发现场，而不是只在产物目录内工作。

建议事项：

- 将 MCP 从“可选 bridge”升级为“资产接入层”的一等能力。
- 为 Skill / MCP / CLI 统一定义 asset manifest。
- 把 `project_verify` 升级为标准 Action，规划阶段可显式选择 `verify_project`、`run_tests`、`run_app_probe`。
- 为工程任务引入 workspace / worktree 隔离模型，区分 artifact sandbox、repo workspace、external workspace。
- 在任务输出中展示“执行了哪些动作、哪些动作被跳过、验证是否通过”。

推荐 manifest 草案：

```yaml
id: enterprise.gitlab
kind: mcp
display_name: GitLab Enterprise
enabled_by_default: false
scope:
  tenants: ["default"]
  modes: ["code", "qa"]
permissions:
  read: ["projects", "merge_requests"]
  write: ["comments"]
health_check:
  tool: list_projects
  timeout_sec: 5
routing_hints:
  intents: ["code_review", "ci_investigation", "release_note"]
context_policy:
  load_schema: on_demand
  max_schema_tokens: 4000
audit:
  redact_fields: ["token", "password", "secret"]
```

实现原则：

- schema 按需加载，不能把全部工具定义常驻塞进 prompt。
- 资产必须有健康探针、权限声明、审计脱敏和租户边界。
- 写操作必须绑定 Turn contract，不能仅凭模型文本声称完成。

### P2：模型路由与多智能体编排

目标是提升复杂任务的吞吐、准确率和成本控制。

建议事项：

- 建立 purpose 级模型路由矩阵，例如 routing、planning、retrieval_rewrite、reasoning、writing、verification、reflection。
- 为 Auto 路由记录决策理由：任务类型、上下文规模、成本上限、延迟要求、合规要求。
- 将 supervisor graph 从固定 decompose/worker/merge 扩展为可配置 workflow。
- 增加结构化 fan-in 契约，worker 输出必须可校验。
- 引入对抗式校验与 judge panel，用于架构评审、代码审查、复杂故障定位。

推荐 workflow 原语：

```python
WorkflowSpec(
    id="codebase_review",
    stages=[
        FanOut(role="explore", shards="path_groups", output_schema="FindingList"),
        FanOut(role="critic", input="findings", output_schema="CritiqueList"),
        FanIn(role="judge", strategy="rank_by_severity"),
    ],
    limits=WorkflowLimits(max_workers=8, max_total_workers=64, timeout_sec=900),
)
```

实现原则：

- 子代理不能只返回面向人类的自然语言，应尽量返回 schema 化对象。
- fan-in 必须处理 partial failure，单个 worker 失败不能直接污染全局结论。
- 多智能体任务必须有预算上限、并发上限、取消传播和审计 trace。

### P3：产品体验、调度自治与长期运营

目标是让系统成为持续运行的研发协作者，而不是只处理当前请求。

建议事项：

- 统一结构化澄清事件，例如 `clarification_requested`，前端渲染为单选/多选/自由输入。
- 建立“会话内任务”和“持久调度任务”的边界模型。
- 为 scheduler 增加任务所有权、租户隔离、过期策略、通知策略、失败摘要。
- 提供生产配置 profile：local、docker、staging、production。
- 增加运维面板指标：活跃任务、平均 Turn 耗时、工具失败率、检索命中率、压缩保真率、验证通过率。

实现原则：

- 调度任务不能绕过统一入口，唤醒后仍应产生标准事件语义。
- 后台任务必须可查询、可取消、可重放关键 trace。
- 前端展示只消费后端状态投影，不复制业务判定逻辑。

## 6. 分项工程方案

### 6.1 统一工具收敛门控

问题：当前工具执行后的路由已有 `evaluate_convergence`，但仍保留 legacy fallback。随着工具种类增加，这类分叉会导致“有时继续执行、有时进入上下文治理”的行为难以解释。

建议：

- 将工具循环收敛抽象为唯一返回对象 `ConvergenceDecision`。
- 所有 pending action、Turn contract、非重试失败、writing force write 都作为输入信号进入 converge，而不是在 router 中散落判断。
- router 只负责消费 `next`，不再承载业务判断。

建议结构：

```python
class ConvergenceDecision(BaseModel):
    next: Literal["tool_execution", "incremental_planning", "context_governance", "dead_letter"]
    reason: str
    pending_actions: list[str] = []
    blocking_errors: list[str] = []
    contract_fulfilled: bool
```

验收标准：

- `route_after_tool` 的分支数量明显下降。
- 每个 `next` 都有单测覆盖。
- trace 中可看到收敛原因、pending action 和 contract 状态。

### 6.2 验证升格为一等 Action

问题：工程执行路径中已有 verify，但普通工具任务、仓库任务、文档任务的验证粒度不统一。

建议：

- 在 planning action schema 中加入标准验证动作。
- verification node 消费所有验证回执，形成统一 `verification_report`。
- output node 根据报告决定是否允许表述“已验证”。

建议 Action：

```json
{
  "type": "verify",
  "name": "run_tests",
  "args": {
    "command": "pytest tests/services/test_converge.py",
    "timeout_sec": 120,
    "scope": "repo"
  },
  "required_for_delivery": true
}
```

验收标准：

- 工程类交付至少包含一个验证回执或明确说明未验证原因。
- 验证失败时进入修复预算或失败分流，不直接输出成功态。
- verification 报告进入 SSE trace 和最终任务状态。

### 6.3 企业资产接入层

问题：Skills、MCP、CLI、研发流程目前分散在不同配置和服务中，缺少统一资产视图。

建议：

- 增加 `AssetRegistry`，统一管理 Skill、MCP server、CLI wrapper、workflow。
- 每个资产有 manifest、schema loader、health check、permission policy、audit policy。
- task planning 只看到经过模式、租户、权限过滤后的能力集合。

建议目录：

```text
config/assets/
  skills/*.yaml
  mcp/*.yaml
  cli/*.yaml
  workflows/*.yaml
app/services/assets/
  registry.py
  manifest.py
  health.py
  permissions.py
  schema_loader.py
```

实现原则：

- 能力发现和能力调用分离。
- 工具 schema lazy-load。
- 资产启用走灰度，默认最小权限。

### 6.4 Auto 模型路由

问题：当前已有 purpose 级 token 与 timeout，但用户和运维侧难以理解某次调用为什么选择某个模型。

建议：

- 在配置中增加 model routing policy。
- 每次 LLM 调用记录 `model_route_decision`。
- 为不同 purpose 建立评测集和成本/延迟阈值。

配置草案：

```yaml
model_routing:
  enabled: true
  default_strategy: balanced
  purposes:
    routing:
      prefer: low_latency
      max_latency_ms: 3000
    planning:
      prefer: reasoning_quality
      min_context_window: 128000
    writing:
      prefer: long_output
      max_tokens: 16384
    verification:
      prefer: deterministic
      temperature: 0
```

验收标准：

- trace 中包含模型选择原因。
- 可按 purpose 统计 token、耗时、失败率。
- Auto 路由策略变更有 replay / golden 对比。

### 6.5 结构化澄清与人机协作

问题：复杂任务中，系统需要在“直接规划”和“先澄清”之间做稳定选择。参考系统强调结构化选择题，这一点可增强前端体验和任务准确率。

建议：

- 增加 `clarification_requested` 事件块。
- 规划节点遇到多方案、高风险、信息缺失时输出结构化问题。
- 用户回答后作为补充约束事件进入同一 Turn 或新 Turn。

事件草案：

```json
{
  "type": "clarification_requested",
  "questions": [
    {
      "id": "deployment_target",
      "prompt": "本次优化优先面向哪个部署环境？",
      "options": [
        {"id": "local", "label": "本地开发"},
        {"id": "docker", "label": "Docker 部署"},
        {"id": "production", "label": "生产环境"}
      ],
      "allow_multiple": false
    }
  ]
}
```

实现原则：

- 澄清不是问“计划是否可以”，而是补齐会影响方案的事实。
- 问题数量受限，默认 1 到 3 个。
- 用户始终可提供自定义答案。

### 6.6 Supervisor Workflow 化

问题：当前 supervisor 子图提供了多 worker 骨架，但离可复用的企业工作流还有距离。

建议：

- 将 supervisor 拆解为 workflow runtime。
- 支持 `fan_out`、`fan_in`、`pipeline`、`judge`、`loop_until_dry`。
- worker 输出统一 schema，merge 阶段只处理结构化结果。

适用场景：

- 大代码库架构审查。
- 多模块迁移影响面扫描。
- CI 失败根因并行定位。
- 文档、代码、测试一致性检查。
- 安全、性能、正确性多视角评审。

实现原则：

- workflow 是确定性控制流，worker 是模型能力。
- worker 失败、超时、取消都必须向父任务传播结构化状态。
- 合并结果必须保留来源 worker、证据路径和置信度。

### 6.7 调度自治模型

问题：scheduler 已存在，但需要明确它与 Turn、Session、Run 的关系。

建议：

- 调度触发只生成事件，不直接调用业务节点。
- durable schedule 必须持久化 owner、tenant、expires_at、last_result、failure_count。
- 调度任务输出应进入通知、任务状态或摘要，不默认污染活跃聊天上下文。

建议状态：

```python
class ScheduledTaskState(BaseModel):
    schedule_id: str
    tenant_id: str
    owner_session_id: str | None
    trigger: str
    durable: bool
    expires_at: datetime | None
    last_run_id: str | None
    last_status: Literal["success", "failed", "cancelled", "expired"]
    failure_count: int
```

验收标准：

- 重启后 durable 任务可恢复。
- 非 durable 任务不会跨会话误触发。
- 连续失败有退避与摘要。

### 6.8 文档和配置治理

问题：当前文档能说明架构，但缺少“如何部署、如何开关、如何灰度、如何排障”的运营文档。配置项较多，团队容易误配。

建议：

- 新增 `docs/config_profiles.md`：解释 local、docker、staging、production 推荐配置。
- 新增 `docs/operations.md`：任务卡死、工具失败、检索低命中、上下文过载、验证失败的排障路径。
- 将 `README.md` 阅读顺序扩展为现状架构、RAG/Skills、优化路线、部署运营。
- 对高风险 feature flag 标注默认值、适用场景、回滚方式。

实现原则：

- 文档与配置同改。
- 新增 feature flag 必须说明默认值、灰度策略、指标和回滚方式。
- 文档不编造未实现能力；规划能力要明确标注“建议”或“目标状态”。

## 7. 可度量验收指标

| 指标 | 目标 | 观测来源 |
|---|---|---|
| 工具收敛可解释率 | 100% 工具回合都有 convergence reason | route trace / audit log |
| 工程交付验证覆盖率 | 工程任务 >= 90% 带验证回执或未验证原因 | verification report |
| MCP/Skill 健康可见性 | 启用资产 100% 有 health check 状态 | AssetRegistry |
| Auto 路由可追溯率 | LLM 调用 100% 记录 routing decision | llm trace |
| 上下文保真率 | 关键任务 evidence fidelity 不低于阈值 | context trace |
| 调度任务恢复率 | durable 任务重启后可恢复执行 | scheduler tests |
| API 回归覆盖 | 核心管理 API 均有 happy path 与失败 path | tests/api |
| Workflow 失败隔离 | 单 worker 失败不导致无解释全局失败 | supervisor/workflow tests |

## 8. 非目标

短期不建议做以下事情：

- 不重写 LangGraph 主图。当前 frozen spine 是优势，应在其上演进。
- 不把所有参考系统工具一次性照搬进项目。应先建立资产接入规范，再逐类接入。
- 不绕过上下文治理直接向 prompt 注入大段工具 schema 或文件内容。
- 不为了 Auto 路由牺牲可解释性。模型选择必须可追踪、可评测。
- 不把验证做成输出文案。验证必须是结构化动作和结构化回执。

## 9. 建议落地顺序

1. **两周内**：统一工具收敛门控、补关键节点测试、整理 verification report。
2. **一个月内**：实现资产 manifest 与 MCP health check，验证 Action 标准化，补 API 测试。
3. **两个月内**：上线 model routing trace、生产配置 profile、结构化澄清事件。
4. **一个季度内**：Supervisor workflow 化，支持 fan-out / fan-in / judge，建立多智能体评测集。

## 10. 总结

当前项目的核心竞争力是运行时治理深度，而不是单点工具数量。参考系统提供了产品化 Agent 的能力方向，但本项目更适合采用“控制面优先”的演进路线：先稳定收敛、验证、资产、模型和调度的工程契约，再扩展 IDE、多智能体和企业流程能力。

最终目标是让系统具备三个特征：

- **可托付**：能明确知道自己在做什么、做到了哪一步、失败在哪里。
- **可扩展**：Skill、MCP、CLI、workflow、模型都能按统一契约接入。
- **可运营**：每次任务、每个工具、每次模型调用、每个验证结论都有 trace、指标和回归测试支撑。
