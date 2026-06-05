# 意图观测模型化与 OMAW / 旧 Mission 退役迁移方案

> 状态：**completed**（Phase 0–3 代码与文档已落地；生产灰度与 legacy 命中率清零为运维验收项）  
> 日期：2026-06-04（实施完成：2026-06-05）  
> 依据：[`docs-private/RESUME_PROJECT_WRITEUP_AGENT_LANGRAPH.md`](../docs-private/RESUME_PROJECT_WRITEUP_AGENT_LANGRAPH.md)、[`docs/ADR_MISSION_LIFECYCLE_V2.md`](ADR_MISSION_LIFECYCLE_V2.md)、[`docs/ADR_CONTEXT_GOVERNANCE.md`](ADR_CONTEXT_GOVERNANCE.md)、[`docs/MISSION_EXECUTION_CONTROL.md`](MISSION_EXECUTION_CONTROL.md)、[`docs/IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md)

---

## 1. 目标与结论

本文只回答两个工程决策，并给出长期工程化执行方案：

1. **观测意图必须使用模型，但不能退回成“全靠模型自由判断”。**
2. **OMAW 与旧 Mission / 旧写作路径必须完成架构收敛：新架构成为唯一长期路径，旧架构进入受控退役并最终移除。**

对应到工程原则：

- **意图观测层**采用“显式模式优先 + 结构信号预判 + 小模型/主模型判定 + 路由审计回看”的四层结构；
- **执行层**采用“单轮执行图负责非 mission 任务，Mission 图只承接 OMAW 长任务控制面”的唯一分工；
- **文档、代码、测试、配置、监控**必须一起收敛，不能只改其中一层。

这也是更接近 Cursor / Copilot 一类成熟工程的做法：

- 先做交互模式与意图判断，再决定后续执行器；
- 意图判断本身是模型能力，但必须被 contract、状态和审计包住；
- 一旦主路径已经确定，就不再允许多个历史架构长期并存。

### 1.1 本文新增关注点

相较于单纯“给结论”，本文补充三类更贴近工程落地的内容：

1. **工程细节**：模块边界、状态字段、观测入口、灰度切换方式；
2. **实施技巧**：如何分阶段接线、如何避免一次性重构导致系统失稳；
3. **误区避免**：哪些做法表面快、长期却会把系统重新带回旧架构。

---

## 2. 现状判断

### 2.1 关于“观测意图需使用模型”的现状

当前仓库已经有“先模式分流，再决定规划”的方向，这一点在 [`docs-private/RESUME_PROJECT_WRITEUP_AGENT_LANGRAPH.md`](../docs-private/RESUME_PROJECT_WRITEUP_AGENT_LANGRAPH.md) 第 5 章已经讲清楚，也已在 [`app/services/pre_planning.py`](../app/services/pre_planning.py:114) 落地为 pre-planning pipeline。

但目前实际仍是**混合式**：

1. 一部分意图判断来自显式 `interaction_mode` 与结构规则，见 [`parse_explicit_interaction_mode()`](../app/services/pre_planning.py:40)；
2. 一部分来自 route audit / structural inference，见 [`seed_pre_planning_route_audit()`](../app/services/pre_planning.py:98)；
3. 一部分来自后续 planning / llm intent 语义判断，这在项目说明中已被明确写入；
4. Mission 控制面还有基于 continue/steer/pattern 的机械决策，见 [`is_mechanical_resume_decision()`](../app/services/mission_execution.py:45)。

这说明当前系统并不是“完全没做模型化意图观测”，而是**还没有把模型判意图定义为正式的一层能力合同**。

当前缺口不在“是否有 classifier”，而在：

- 缺少统一术语：到底什么叫 intent observation、mode resolution、planning decision，各自边界不够硬；
- 缺少固定调用点：哪些回合必须跑模型判意图，哪些回合必须禁止；
- 缺少 purpose-specific context contract：意图判定该吃什么上下文，尚未像 [`docs/ADR_CONTEXT_GOVERNANCE.md`](ADR_CONTEXT_GOVERNANCE.md) 那样被硬化成唯一架构；
- 缺少专门评测：目前实现状态文档强调了 pre-planning 已落地，但还没有把“intent observation 准确率 / 误路由率 / 模式切换回退率”作为一级评估指标。

### 2.2 关于 OMAW 与旧 Mission 的现状

当前文档层面其实已经给出了很强的方向约束：

- [`docs/ADR_MISSION_LIFECYCLE_V2.md`](ADR_MISSION_LIFECYCLE_V2.md) 明确写了 **`execution_mode = mission_oma` 是唯一长期写作运行时**；
- [`docs/MISSION_EXECUTION_CONTROL.md`](MISSION_EXECUTION_CONTROL.md) 已把 Mission 从“长文功能”提升为控制面；
- [`docs/IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md) 也表明 OMAW 已经闭环落地，且 `writing_llm_decide` 已被标成遗留。

但从代码与系统形态看，旧路径仍未完全退场，主要体现在：

1. 旧 `Mission` 命名仍覆盖了“控制面”和“旧写作执行路径”两层语义，导致外部理解容易混淆；
2. [`app/runtime/mission_graph.py`](../app/runtime/mission_graph.py:43) 仍沿用通用 Mission 图命名，而不是更清晰地区分“Mission control plane”与“OMAW worker orchestration”；
3. [`docs/MISSION_EXECUTION_CONTROL.md`](MISSION_EXECUTION_CONTROL.md) 第 147–155 行仍保留“模型可选 writing_phase”的边界描述，这说明兼容旧路径的开关尚未完全清除；
4. [`docs/IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md) 第 223 行明确写了“写作阶段（模型自选）🔶 遗留”；
5. 对外讲解文档仍容易把“Mission 图循环写作”与“OMAW 编排式写作”混讲，形成双语义。

因此，当前真正的问题不是“没有新架构”，而是：

**新架构已经成立，但旧架构仍作为兼容层残留在命名、配置、代码分支和认知模型里。**

### 2.3 根因归纳

从工程视角看，这两个问题共用一套根因：

1. **语义层未收敛**：一个词同时指多层概念，例如 Mission 既指控制面又被历史上用于执行路径。
2. **调用层未收敛**：虽然已有 [`run_pre_planning_pipeline()`](../app/services/pre_planning.py:114)，但“判意图”和“做规划”的调用边界还不硬。
3. **状态层未收敛**：一些关键判定仍散落在 `input_payload` 临时字段里，缺少统一结构化模型。
4. **观测层未收敛**：系统更容易观测执行后发生了什么，较少观测“为什么在前面这样路由”。
5. **兼容层未收敛**：历史开关还在，导致代码总能偷偷回退到旧路径。

---

## 3. 两个问题的工程化长期定性

### 3.1 问题一：为什么观测意图必须使用模型

对于复杂 agent runtime，意图观测不是简单分类，而是回答四个问题：

1. 用户当前是在问答、工程交付、写作、还是 mission 控制；
2. 这是 stay / switch / isolate 中哪一种会话关系；
3. 当前回合是执行、重规划、确认，还是机械续跑；
4. 是否应该进入窄执行器，还是仍需要 planning。

其中第 1、2 问有明显语义歧义，仅靠规则很快失效。真实用户输入经常会出现：

- 既像问问题，又在要求改代码；
- 既像继续写作，又在修改目标；
- 表面写着“继续”，本质却是在插入新的 steer；
- 一句话里同时包含产物类型、交付动作、范围约束和风险偏好。

这类场景如果只靠规则，最终会出现两个问题：

- **规则表爆炸**：维护成本像 DSL，一直追用户表达；
- **误路由不可解释**：因为规则只能命中表面 pattern，无法解释深层语义。

因此，意图观测必须让模型参与。

但“使用模型”不等于“让模型自由决定一切”。长期成熟做法应该是：

- 显式模式是最高优先级；
- 结构信号用于低成本预判与兜底；
- 模型负责处理歧义与隐含语义；
- route audit / reflection 负责发现误路由并纠偏；
- 任何模型判定都必须写入状态并可观测。

### 3.2 问题二：为什么 OMAW / 旧 Mission 必须收敛到唯一架构

成熟工程不会长期维持“同一问题两套主路径”：

- 文档很难保持一致；
- 新 feature 不知道该接哪条链路；
- 测试矩阵爆炸；
- 运维和指标口径分裂；
- 用户行为会在不同路径上表现不一致。

对当前项目尤其如此，因为 Mission 不只是“写作功能”，而是运行时控制平面；而 OMAW 不只是“另一个写作模式”，而是写作域的唯一执行语义。

如果旧 `writing_phase`、旧 narrator/reasoning 式写作、旧 planning→reasoning→正文路径继续保留，就会带来三类长期风险：

1. **架构漂移**：任何人都可能为了省事把写作逻辑重新塞回主图；
2. **认知漂移**：文档和代码都说 OMAW 唯一，但真正 debug 时又冒出旧路径；
3. **质量漂移**：[`FactBundle`](app/domain/fact_bundle.py:1)、[`ReviewVerdict`](app/domain/review_verdict.py:1)、[`WorkerExecutionPolicy`](app/domain/worker_execution_policy.py:1) 等新约束无法成为强制门槛。

因此，必须定义“旧架构退役计划”，而不是只写“推荐使用新架构”。

---

## 4. 目标架构

## 4.1 北极星

长期收敛后的统一结构应为：

```text
User/API/Web
  → explicit interaction mode
  → intent observation model
  → mode resolution / turn kind
  → execution path selection
      - qa / engineering: single graph or bounded executor
      - manuscript long task: mission control plane only
            → OMAW orchestrator
            → capability-bounded workers
            → FactBundle + ReviewVerdict + WorkerExecutionPolicy
  → output / memory / audit
```

### 4.2 意图观测层的正式分层

建议把当前 pre-planning 继续升级为四层：

1. **L0 显式选择层**  
   Web/API 指定 `interaction_mode` 时优先消费，见 [`parse_explicit_interaction_mode()`](../app/services/pre_planning.py:40)。

2. **L1 结构预判层**  
   由 route audit、payload 结构、session 状态、mission active、turn policy 等进行低成本预判，见 [`seed_pre_planning_route_audit()`](../app/services/pre_planning.py:98)。

3. **L2 模型观测层**  
   新增统一 `intent observation` 模型调用，只解决歧义问题，不直接产出自由散文计划。输出必须是结构化对象，例如：
   - `intent_kind`
   - `target_mode`
   - `turn_kind_candidate`
   - `session_relation = stay|switch|isolate`
   - `needs_planning`
   - `confidence`
   - `evidence_spans`

4. **L3 路由审计层**  
   继续保留 route audit / reflection 对误路由进行后验校正，但它只能纠偏，不能替代前置 intent observation。

### 4.3 写作运行时的唯一结构

写作域长期只保留下面这套：

- 长篇写作进入 Mission 控制图；
- Mission 控制图只负责 decide / act / observe / eval / finalize；
- `mission_act` 不再承载旧式自由写作切 phase 逻辑；
- 真正执行由 OMAW worker orchestration 完成；
- Writer / Reviewer / Editor / Planner 统一受 [`WorkerExecutionPolicy`](app/domain/worker_execution_policy.py:1) 约束；
- 所有写作与审阅输入必须先构造 [`FactBundle`](app/domain/fact_bundle.py:1)；
- 所有质量判定统一落到 [`ReviewVerdict`](app/domain/review_verdict.py:1)。

### 4.4 推荐的数据流与状态边界

建议新增一层明确的结构化状态，而不是继续把所有判断散落在 `input_payload`：

```json
{
  "intent_observation": {
    "version": "v1",
    "source": "llm|explicit|structural|hybrid",
    "intent_kind": "qa|engineering|writing|mission_control",
    "target_mode": "qa_mode|engineering_mode|manuscript_mode",
    "session_relation": "stay|switch|isolate",
    "turn_kind_candidate": "narrate_only|steer_replan|steer_execute|mission_step_execute|mechanical_continue",
    "needs_planning": true,
    "confidence": 0.87,
    "reasons": ["explicit_mode=auto", "mission_active=true"],
    "trace_id": "..."
  }
}
```

建议把它视为与 [`mode_resolution`](../app/services/mode_resolution.py:31) 平级的正式对象，而不是临时诊断字段。

### 4.5 模块职责边界图

为避免未来再把职责混回去，建议固定如下分工：

| 层 | 负责什么 | 不负责什么 |
|---|---|---|
| intent observation | 理解本轮意图与会话关系 | 生成执行计划、生成正文 |
| mode resolution | 把意图映射为 mode contract | 解释用户长文本语义 |
| planning | 在已知 mode 中做路径规划 | 猜当前属于哪个 mode |
| mission control | 长任务推进、pause/resume/gate | 自由生成写作正文 |
| OMAW workers | 受能力约束地写作/审阅/润色 | 决定全局控制策略 |
| observation | 记录执行后事实 | 替代前置 intent 判定 |

---

## 5. 执行方案一：建立“意图观测模型层”

### 5.1 设计原则

1. **模型只做意图观测，不做全局 planning。**
2. **模型输出必须结构化，不能返回自由文本给路由层解释。**
3. **观测输入必须使用 governed context，而不是裸 transcript。**
4. **有显式模式时，模型只能补充 confidence 和 stay/switch/isolate，不得推翻用户明确选择，除非触发安全/契约级阻断。**
5. **mission active 时，观测模型必须理解 Mission control 语义，而不仅是通用 chat 分类。**

### 5.2 推荐新增模块边界

建议新增：

- `app/services/intent_observation.py`  
  统一结构化意图观测入口；
- `app/domain/intent_observation.py`  
  定义 `IntentObservationResult`、`SessionRelation`、`TurnIntentClass`；
- `app/services/intent_observation_policy.py`  
  决定哪些回合必须调用模型、哪些可跳过；
- `app/services/intent_observation_eval.py`  
  离线评测与 baseline；
- `tests/services/test_intent_observation*.py`  
  单测与 golden。

### 5.3 与现有链路的整合方式

建议把 [`run_pre_planning_pipeline()`](../app/services/pre_planning.py:114) 改造成：

```text
explicit mode parse
→ structural route audit seed
→ intent observation policy decision
→ if needed: intent observation model invoke
→ merge structured result into payload.route_audit / mode_resolution seed
→ apply mode contract
→ downstream planning or thin executor
```

其中关键改动点：

1. `resolve_target_mode` 的输入不再只是结构推断结果，还要吃 `intent_observation_result`；
2. route audit 分为 `pre_observation` 和 `post_execution` 两类，避免概念混杂；
3. [`should_skip_planning_llm()`](../app/services/pre_planning.py:154) 的判断应依赖 `needs_planning`，而不是只依赖工程模式和若干条件拼接；
4. Mission session 中，continue / steer / confirm 要先过意图观测策略，再决定是否能走机械续跑。

### 5.4 调用策略

建议分级调用，避免每轮都打重模型：

#### A. 必须调用模型的场景

- `interaction_mode=auto`；
- 当前输入同时含“问题 + 操作指令”；
- mission active 且当前消息不是纯确认；
- route audit confidence 低于阈值；
- 上一轮发生 misroute / reflection replan；
- 需要判断 `stay|switch|isolate`。

#### B. 可跳过模型的场景

- 用户显式选择了模式，且输入与该模式高度一致；
- 纯 `confirm: true`；
- 明确的 `/resume`、`/pause`、`/cancel` API；
- 已经持有有效 `execution_grant` 的机械续跑回合。

### 5.5 上下文治理要求

意图观测模型的上下文不应吃全量历史，而应由 [`prompt_context_gateway`](../app/services/prompt_context_gateway.py:1) 新增 `purpose=intent_observation` 专用策略：

必须包含：

- 当前用户输入；
- 最近 1–3 轮摘要；
- 当前 mission 状态摘要；
- 当前 mode / session relation 候选；
- 未消费 steer / gate / execution grant 摘要。

默认不包含：

- 长文件正文；
- 大段检索结果；
- 工具日志全文；
- 无关历史聊天原文。

这与 [`docs/ADR_CONTEXT_GOVERNANCE.md`](ADR_CONTEXT_GOVERNANCE.md) 的长期方向一致：不同 purpose 必须有独立合同。

### 5.6 工程细节：推荐接口草案

建议把意图观测接口固定为窄接口，避免未来不同节点自行拼 prompt：

```python
class IntentObservationResult(BaseModel):
    version: str = "v1"
    source: str
    intent_kind: str
    target_mode: str
    session_relation: str
    turn_kind_candidate: str | None = None
    needs_planning: bool = True
    confidence: float = 0.0
    reasons: list[str] = []
    trace_id: str | None = None


def observe_intent(
    state: AgentState,
    *,
    explicit_mode: str | None,
    route_audit_seed: dict[str, Any],
) -> IntentObservationResult:
    ...
```

这里的关键技巧是：

- **不要**直接返回 dict 任由上层乱改；
- **不要**让下游节点再解释一次自然语言分类结果；
- **要**让 `trace_id` 贯穿日志、metrics、reasoning trace 和后续 route audit。

### 5.7 工程细节：模型选择策略

推荐采用双层策略，而不是默认用最贵模型：

1. **轻量模型优先**：处理大多数 auto mode 分类；
2. **主模型升级**：仅在低置信度、高风险、多意图混杂时触发；
3. **显式模式直通**：有明确模式且高一致时直接跳过模型。

这样做的原因：

- 意图观测本质是高频、短上下文、结构化任务；
- 若直接绑定主模型，系统会把大量预算消耗在“进入哪个执行器”而不是“真正解决任务”；
- 这与 [`resource_budget`](../app/services/resource_budget.py:1) 的治理目标一致。

### 5.8 工程细节：回写与审计方式

建议把观测结果同时写入三处：

1. `state.intent_observation`：作为正式状态；
2. `input_payload.route_audit.intent_observation_summary`：给旧观察面兼容；
3. turn event log / audit log：用于误路由回放。

推荐记录这些审计字段：

- `observation_source`
- `confidence`
- `explicit_mode_present`
- `mission_active`
- `session_relation`
- `needs_planning`
- `model_name`
- `latency_ms`
- `fallback_used`

### 5.9 实施技巧

#### 技巧 A：先加只读观测，不先改决策

第一阶段可先把 `intent_observation_result` 写出来，但仍不真正接管 `resolve_target_mode`。先观察两周指标，再切换主决策来源。这能显著降低误路由直接打到生产链路的风险。

#### 技巧 B：做 shadow mode

建议在 auto mode 下并行运行：

- 当前正式决策；
- 新 intent observation 建议决策。

只做比对，不生效。对比差异样本是最有价值的训练/修正规则来源。

#### 技巧 C：先压缩词汇，再压缩逻辑

先把文档、日志、trace 里的 `intent`、`mode`、`turn_kind` 词汇对齐，再接线改逻辑。否则后面 debug 会出现“名字都像一个东西，实际不是一个东西”的问题。

### 5.10 评测与门禁

建议新增以下指标：

- `intent_observation_accuracy`
- `mode_resolution_misroute_total`
- `mission_mechanical_resume_false_positive_total`
- `stay_switch_isolate_disagreement_total`
- `planning_skip_wrongly_total`

并建设三套 golden：

1. **QA / engineering / writing 基础分类集**；
2. **mission 中 continue / steer / confirm / replan 区分集**；
3. **Cursor/Copilot 风格混合指令集**，例如“解释一下这个 bug，然后顺手帮我修掉”。

### 5.11 常见误区与避免方式

#### 误区 1：把意图观测直接并入 planning

短期看省一个模型调用，长期会造成：

- planning 提示词越来越脏；
- mode 判定与 plan 生成互相污染；
- 无法独立评测“判对了没有”。

正确做法：**intent observation 与 planning 分层，planning 只在已知 mode 内工作。**

#### 误区 2：把 continue / steer 全部规则化

这会在 Mission 场景中产生大量误判，尤其是用户用自然语言说“继续，但先把上一章节奏收紧一些”时。

正确做法：**机械控制只处理明确控制信号，语义混合输入仍交给意图观测层。**

#### 误区 3：让意图模型直接给出自由文本理由并由上层解析

这会把结构化层重新变成 prompt parsing。

正确做法：**只接受 schema 化输出，自然语言理由仅作为 trace 附件。**

---

## 6. 执行方案二：OMAW / 旧 Mission 全量迁移与退役

### 6.1 迁移目标

把“Mission 是长任务控制面，OMAW 是写作域唯一执行架构”从文档结论，变成代码事实。

### 6.2 迁移原则

1. **保留控制面，移除旧执行面。**  
   也就是保留 [`build_mission_graph()`](../app/runtime/mission_graph.py:43) 的控制循环，但移除其对旧写作自由路径的兼容依赖。

2. **先切默认，再清兼容，再删代码。**  
   不要一步硬删，而是按 feature flag、只读兼容、最终删除三阶段推进。

3. **先统一词汇，再统一代码。**  
   否则开发者会持续把旧概念带回实现层。

4. **迁移完成标志必须可验证。**  
   不能只说“原则上不再使用”。

### 6.3 需要被移除或降级的旧内容

#### A. 配置与开关

- `mission.writing_llm_decide`：进入 deprecated，下一阶段默认强制 false，最终删除；
- 所有允许“写作任务不构造 `FactBundle` 直接生成正文”的兼容开关；
- 所有允许在主图 `planning → reasoning` 中直接完成 mission 写作的入口。

#### B. 代码路径

需要逐步清理：

- 旧 `writing_phase` 自选分支；
- `mission_act` 内联 narrator 式写作分支；
- 旧式以 reasoning 代替 worker 执行的写作逻辑；
- 任何绕过 `ReviewVerdict` 的章节验收路径；
- 任何写作任务仍通过 `run_pipeline_request` 默认落到非 OMAW 执行器的入口。

#### C. 文档与认知路径

需要统一替换以下说法：

- “Mission 图负责写作、审查、润色的全部执行” → 改为“Mission 图是控制面，写作执行由 OMAW workers 完成”；
- “长文任务默认是 Mission 循环” → 改为“长文任务默认是 Mission control + OMAW orchestration”；
- “writing_phase 是核心阶段语义” → 改为“`writing_phase` 仅作迁移兼容别名，不再是主架构词汇”。

### 6.4 分阶段迁移计划

#### Phase 0：词汇收敛与 ADR 锁定

产出：

- 在所有主文档中统一以下表述：
  - Mission = control plane
  - OMAW = writing execution architecture
  - `writing_phase` = compatibility alias only
- 给旧路径打上明确 `legacy` / `deprecated` 标记；
- 在 README、实现状态、mission 文档中增加“唯一长期路径”总表。

#### Phase 1：默认路径强制收敛

动作：

- 写作任务默认全部走 `mission_oma`；
- 新建任务时若是 manuscript long task，禁止落到旧 single graph 写作；
- `mission_act` 若检测到缺少 `FactBundle` 或 `WorkerExecutionPolicy`，直接进入失败/审计，而不是降级为旧 narrator 写法；
- `mission_finalize` 不再为旧写作路径兜底总结。

验收：

- 新增和回归测试全部以 OMAW 为基线；
- live trace 中不再出现旧写作 phase 自选记录。

#### Phase 2：兼容层封存

动作：

- `writing_llm_decide` 保留只读兼容，但默认 hard-off；
- 任何命中旧分支时写入高优先级审计与 metrics；
- 对旧 API / payload 字段保留读兼容，但内部立即映射到新词汇；
- 文档中将旧路径移入“退役历史”，不再出现在主流程图。

验收：

- 旧路径命中率连续两个版本为 0；
- 所有写作验收记录都包含 `fact_bundle_id` 与标准 `ReviewVerdict`。

#### Phase 3：物理删除

动作：

- 删除旧 `writing_phase` 主决策逻辑；
- 删除旧写作 narrator / reasoning fallback 分支；
- 删除旧配置项、旧测试、旧文档主叙述；
- Mission 相关模块按 control plane / omaw worker 重新整理目录。

验收：

- 代码搜索结果中，`writing_llm_decide` 只出现在迁移记录或 release notes；
- `mission_act` 不再拥有旧写作自由执行语义；
- [`docs/IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md) 中“写作阶段（模型自选）🔶 遗留”项被移除。

### 6.5 工程细节：推荐迁移顺序

推荐按“入口 → 状态 → 执行器 → 清理”的顺序做，而不要按文件夹硬切：

1. 先锁定写作入口判定；
2. 再锁定 `execution_mode` 与 `turn_kind`；
3. 再把 `mission_act` 与 worker bridge 切到硬约束；
4. 最后再删除 legacy path。

这样做的好处是：

- 任一时点都能判断“任务应该去哪”；
- 出问题时回滚点更明确；
- 不会出现入口已经切新、执行器还在走旧分支的半完成状态。

### 6.6 工程细节：建议建立 legacy manifest

建议新增一份简单清单，例如：

```text
legacy mission paths:
- writing_llm_decide
- mission_act.inline_writing_phase
- pipeline_reasoning_writing_fallback
- manuscript_without_fact_bundle
```

并在 CI 中对这份清单做两件事：

1. 这些标识一旦新增，要么写迁移说明，要么拒绝合入；
2. 每个版本统计仍被命中的 legacy path 次数。

这是一种非常实用的实施技巧：**先把“旧东西还剩哪些”资产化，而不是靠记忆追踪。**

### 6.7 工程细节：如何防止“暗回退”

旧架构最容易在两类场景里偷偷回来：

1. 某个异常分支为了兜底，直接调用 reasoning 产正文；
2. 某个新功能为了赶进度，绕过 `FactBundle` 直接喂一段上下文给 writer。

建议设置三道硬门：

- 没有 `FactBundle` 的写作 worker 不允许执行；
- 没有标准 [`ReviewVerdict`](app/domain/review_verdict.py:1) 的章节不允许过 gate；
- 任何写作 fallback 一旦命中，直接打审计事件，而不是静默兜底。

### 6.8 实施技巧

#### 技巧 A：先做兼容映射，再删字段

比如 `writing_phase` 不必第一天就完全删掉，可以先只作为 alias 映射到 `dispatch.to_agent` / `capability`。这样外部旧 payload 还能活，但内部已经不再把它当主语义。

#### 技巧 B：用 fail-closed 代替 fail-open

旧路径退役期间，宁可让“缺少 `FactBundle`”显式失败，也不要“先随便写点正文”。对写作 runtime 来说，fail-open 会把所有新架构约束冲垮。

#### 技巧 C：先把 metrics 接好再删代码

如果没有 [`agent_legacy_mission_path_total`](docs/INTENT_OBSERVATION_AND_OMAW_MIGRATION_PLAN.md) 一类指标，删完代码以后也很难知道是否还有影子入口。

### 6.9 常见误区与避免方式

#### 误区 1：把 OMAW 当成“又一个可选模式”

这会让系统重新回到双轨制。

正确做法：**OMAW 是写作域唯一执行面，不是写作域众多模式之一。**

#### 误区 2：只改文档，不改配置开关

只要 [`writing_llm_decide`](docs/MISSION_EXECUTION_CONTROL.md) 还默认可开，开发者就会继续把它当逃生门。

正确做法：**文档、默认值、CI、监控同时收口。**

#### 误区 3：保留一个“临时 reasoning fallback”

临时 fallback 通常会活得比主架构还久。

正确做法：**所有 fallback 必须结构化、可计数、可删除，并带 sunset 日期。**

---

## 7. 推荐工程改造清单

### 7.1 文档层

建议新增/修改：

- 新增本文档作为专项迁移方案；
- 更新 [`docs/ADR_MISSION_LIFECYCLE_V2.md`](ADR_MISSION_LIFECYCLE_V2.md)，补一节“legacy removal checklist”；
- 更新 [`docs/MISSION_EXECUTION_CONTROL.md`](MISSION_EXECUTION_CONTROL.md)，把 `writing_llm_decide` 边界改成“仅历史说明”；
- 更新 [`docs/IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md)，新增“intent observation model”状态项，并把旧写作路径列入退役追踪；
- 更新 [`docs-private/RESUME_PROJECT_WRITEUP_AGENT_LANGRAPH.md`](../docs-private/RESUME_PROJECT_WRITEUP_AGENT_LANGRAPH.md)，避免再把 Mission 图讲成旧写作执行器。

### 7.2 代码层

建议优先改造这些入口：

1. [`app/services/pre_planning.py`](../app/services/pre_planning.py:114)  
   插入 `intent observation` 结构化调用。

2. [`app/services/mode_resolution.py`](../app/services/mode_resolution.py:31)  
   接受 `intent_observation_result` 作为正式输入，而不是隐式散落在 payload。

3. [`app/runtime/mission_graph.py`](../app/runtime/mission_graph.py:43)  
   保持图结构，但在实现命名与注释上明确“control plane only”。

4. [`app/services/mission_execution.py`](../app/services/mission_execution.py:45)  
   机械续跑仅处理控制面信号，禁止侵入语义判意图。

5. [`app/services/observation.py`](../app/services/observation.py:16)  
   继续保留 observation 作为执行后事实层，但它不应替代前置 intent observation 模型。

### 7.3 配置层

建议新增或调整：

- `intent_observation.enabled`
- `intent_observation.shadow_mode`
- `intent_observation.low_confidence_threshold`
- `intent_observation.primary_model`
- `intent_observation.fallback_model`
- `mission.allow_legacy_writing_path=false`
- `mission.require_fact_bundle=true`
- `mission.require_review_verdict=true`

配置层的工程技巧是：**新能力先以可灰度开关接入，但 legacy path 要用“只减不增”的方式管理。** 新开关可以增加，新 legacy 开关原则上禁止增加。

### 7.4 测试层

至少新增：

- 意图观测结构化单测；
- auto 模式下 QA / engineering / writing 路由 golden；
- mission active 时 continue / steer / confirm / intervention 路由 golden；
- OMAW 唯一路径回归测试；
- legacy 配置开关命中即告警测试。

### 7.5 监控层

建议增加 Prometheus / 审计指标：

- `agent_intent_observation_total`
- `agent_intent_observation_fallback_total`
- `agent_legacy_mission_path_total`
- `agent_writing_without_fact_bundle_total`
- `agent_review_verdict_missing_fact_bundle_total`

其中后 3 项应被视为架构违规指标，而不是普通业务指标。

### 7.6 发布层

建议按三类环境推进：

1. **dev**：shadow mode + 审计全开；
2. **staging**：intent observation 接管 auto mode，legacy path 允许但强告警；
3. **prod**：按流量灰度接入，先 5% → 20% → 50% → 100%。

每个阶段都要有回滚条件，例如：

- misroute rate 超阈值；
- Mission 继续/插话误判上升；
- 写作任务出现无 `FactBundle` 的执行事件。

---

## 8. 与 Cursor / Copilot 式工程成熟做法的对齐

如果按长期成熟体系看，当前项目应明确对齐以下实践：

### 8.1 交互模式先行

像 [`app/services/pre_planning.py`](../app/services/pre_planning.py:1) 里已经写出的方向一样，先确定 `chat / engineering / writing`，再决定是否需要 planning。这符合 IDE agent 的现实交互：

- 用户有时是在问；
- 有时是在执行；
- 有时是在切换任务域；
- 系统必须先知道自己当前扮演什么执行器。

### 8.2 意图识别是模型能力，但必须受约束

Cursor / Copilot 类系统不会只靠关键字路由，也不会允许模型无边界自由切图。正确做法就是：

- 小而强约束的结构化分类；
- 与当前工作区/会话态联合判断；
- 有 fallback、有审计、有回退。

### 8.3 主路径唯一

成熟工程会尽快把“实验路径”收束成“唯一主路径”。否则：

- 所有后续 feature 都会乘以路径数；
- 所有 bug 都会先问“你走的是哪条老链路”；
- 文档很快失真。

OMAW 已经是写作域主路径，就应继续完成对旧 Mission 写作路径的退役，而不是长期双轨制。

### 8.4 先观测，再替换

Cursor / Copilot 一类系统在核心路由升级时，通常都会先经历：

- shadow compare；
- 低风险流量灰度；
- 线上样本回放；
- 指标看板对比。

这一点对当前项目尤其重要，因为 Mission control 与 OMAW 写作都不是“错一点也没关系”的普通推荐系统，而是会直接影响执行路径和产物质量的 runtime。

---

## 9. 实施路线图（建议 4 周）

### Week 1：观测与词汇收敛

- 增加 [`IntentObservationResult`](app/domain/intent_observation.py:1) 模型；
- 为 [`run_pre_planning_pipeline()`](../app/services/pre_planning.py:114) 接入 shadow observation；
- 对齐文档中的 Mission / OMAW / `writing_phase` 词汇；
- 增加基础指标与 dashboard 草图。

### Week 2：决策接管与灰度

- 让 auto mode 由 intent observation 正式驱动；
- 给 [`mode_resolution`](../app/services/mode_resolution.py:31) 接入新输入；
- 增加误路由回放样本；
- 在 staging 环境跑 mixed prompt golden。

### Week 3：Mission / OMAW 强制收敛

- `mission_act` 执行前强制检查 `FactBundle` / `WorkerExecutionPolicy`；
- legacy writing path 命中即审计；
- `writing_llm_decide` 改为 hard-off；
- 更新 [`docs/IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md) 状态说明。

### Week 4：物理清理与验收

- 删除旧写作 phase 主决策路径；
- 收敛回归测试与 CI；
- 清理文档主流程图中的旧路径；
- 以 DoD 做最终验收。

---

## 10. 最终建议

### 10.1 对问题 1 的最终建议

**结论：观测意图必须使用模型。**

但建议采用：

- `explicit mode > structural inference > intent observation model > route audit correction`

而不是：

- 纯规则路由；
- 或者纯 planning LLM 顺手猜模式；
- 或者把 Mission control 的 continue/steer 也都当成规则字符串处理。

一句话：

**模型负责理解歧义，规则负责约束边界，状态负责承载结果，审计负责纠偏。**

### 10.2 对问题 2 的最终建议

**结论：OMAW 与旧 Mission 必须全部转向新架构，并明确移除旧架构。**

建议采用三阶段：

- Phase 1：默认全量切新；
- Phase 2：兼容层封存并监控清零；
- Phase 3：删除旧开关、旧路径、旧叙述。

一句话：

**Mission 保留为控制面，OMAW 成为写作域唯一执行面；旧写作 Mission 语义不再作为可选长期架构存在。**

---

## 11. 完成定义（DoD）

当以下条件全部满足时，认为本次架构收敛完成：

1. 存在正式的 `intent observation` 结构化模块与评测集；
2. `pre_planning` 已消费 `intent observation` 结果；
3. `purpose=intent_observation` 已纳入上下文治理；
4. 写作长任务默认且仅走 OMAW；
5. 所有写作 worker 执行前都必须构造 `FactBundle`；
6. 所有章节验收都必须生成带 `fact_bundle_id` 的 [`ReviewVerdict`](app/domain/review_verdict.py:1)；
7. `writing_llm_decide` 与旧写作 phase 主分支已删除或完全退役；
8. 文档主叙述、实现状态、测试门禁、监控指标全部与新架构一致。

### 11.1 DoD 的工程化核验方式

为了避免 DoD 只停留在“看起来完成”，建议每项都绑定具体核验：

- 代码核验：搜索 legacy path 标识、配置项、入口函数；
- 测试核验：golden / integration / oma suite 全绿；
- 指标核验：legacy path 命中率连续两个版本为 0；
- 文档核验：README、ADR、实现状态三处主叙述一致；
- 运行核验：staging/prod 抽样回放无“无 `FactBundle` 写作”与“误机械续跑”事件。

满足这些条件后，项目在这两个问题上才算真正达到工程化长期成熟状态，而不是“方向上已经差不多”。

---

## 12. 执行状态追踪（2026-06-05）

### 12.1 已完成项

| 类别 | 项 | 产物 / 核验 |
|------|-----|-------------|
| **意图观测 L0–L3** | 结构化模块 + policy + eval | `app/domain/intent_observation.py`、`app/services/intent_observation*.py` |
| **pre_planning 接线** | 观测 → mode resolution | `run_pre_planning_pipeline()` 写入 `state.intent_observation` |
| **上下文治理** | `purpose=intent_observation` | `context_policy.py` + `prompt_context_gateway.py` |
| **配置** | 全部 §7.3 开关 | `config/config.yaml`：`intent_observation.*`、`mission.allow_legacy_writing_path` 等 |
| **监控** | §7.5 全部指标 | `metrics_service.py`：`agent_intent_observation_*`、`agent_legacy_mission_path_total` 等 |
| **OMAW 硬约束** | FactBundle / ReviewVerdict | `prepare_worker_execution`、`save_review_verdict` fail-closed |
| **Legacy 封存** | manifest + 审计 | `config/legacy_mission_manifest.yaml`、`legacy_mission_paths.py` |
| **测试** | 单测 + golden + CI | `test_intent_observation*.py`、`test_legacy_*`、`SUITE=intent_observation` |
| **文档** | ADR / README / 实现状态 / RESUME | 见 §7.1 清单 |

### 12.2 DoD 对照

| # | DoD 条件 | 状态 | 备注 |
|---|----------|------|------|
| 1 | intent observation 模块 + 评测集 | ✅ | `intent_observation_eval.py` + 9 golden cases |
| 2 | pre_planning 消费观测结果 | ✅ | 含 `needs_planning` → `should_skip_planning_llm` |
| 3 | `purpose=intent_observation` 上下文治理 | ✅ | |
| 4 | 写作长任务默认 OMAW | ✅ | `execution_mode=mission_oma` |
| 5 | 写作 worker 前 FactBundle | ✅ | `require_fact_bundle=true` |
| 6 | ReviewVerdict 绑定 fact_bundle_id | ✅ | `require_review_verdict=true` + `save_review_verdict` 硬门 |
| 7 | writing_llm_decide 退役 | ✅ | 默认 hard-off；代码保留于 `allow_legacy_writing_path=true` 兼容层 |
| 8 | 文档 / 测试 / 指标一致 | ✅ | 本表 + CI `intent_observation` suite |

### 12.3 运维验收项（非代码阻塞）

以下需在 staging/prod 运行期完成，不影响代码合并：

1. **Shadow 观测**：dev 可设 `intent_observation.shadow_mode=true` 对比 structural vs LLM 2 周；
2. **流量灰度**：prod 5% → 100% 接入 intent observation 主决策；
3. **Legacy 命中率**：连续两个版本 `agent_legacy_mission_path_total` 为 0 后，可物理删除 §6.4 Phase 3 遗留分支；
4. **误路由回放**：抽样验证无「无 FactBundle 写作」与「误机械续跑」事件。

### 12.4 环境推荐配置

| 环境 | intent_observation | legacy path |
|------|-------------------|-------------|
| dev | `shadow_mode: true`（可选） | hard-off + 审计全开 |
| staging | `shadow_mode: false`，接管 auto mode | 允许 legacy 但强告警 |
| prod | 灰度接入 | hard-off |
