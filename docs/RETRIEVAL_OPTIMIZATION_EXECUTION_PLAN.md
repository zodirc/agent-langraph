# 基于 [`question.log`](../question.log) 的检索增强执行优化方案

> **实现状态（2026-06-05）**：Evidence Operating System 八层架构已落地（`retrieval.enable_evidence_pipeline: true`）。135 项 pytest + 10 条 evidence replay 全绿。待办：生产灰度 A/B 面板、`runtime_router` 直连接入。详情见 [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md) §1.4、[`docs-private/RAG.md`](../docs-private/RAG.md) §2.2。

## 1. 背景与目标

[`question.log`](../question.log) 中汇总了 51 个高频问题，核心都指向同一个事实：当前系统的瓶颈已经不只是“能不能召回内容”，而是“能否围绕任务目标，在有限上下文预算内，把正确证据以正确形式、正确优先级、正确边界注入给生成器，并对失败类型进行可观测、可审计、可迭代治理”。

这些问题覆盖了召回噪音、重排不足、chunk 粒度失配、多轮历史污染、证据冲突、预算分配、任务差异化注入、引用可信度、线上评估与失败归因等方面。因此，优化不能只停留在单点调参，而应形成一套端到端执行方案。

本文档目标是给出一套面向现有代码库的分阶段优化执行方案，用于指导后续在 [`app/services`](../app/services) 与 [`app/nodes`](../app/nodes) 相关链路中的实现与治理。

## 2. 问题归类

结合 [`question.log`](../question.log) 中的问题，可以归纳为 8 个优化主题。

### 2.1 召回正确性问题

对应问题：1、2、8、10、17、18、19、40、41。

主要症状：
- 相关内容未召回或召回顺序过低。
- query 很短、很口语、带缩写或配置项时，检索方向跑偏。
- embedding 命中“像但不对”的内容，BM25 命中“词相同但语境不对”的内容。
- 无结果时无法区分“库里没有”还是“query 写坏了”。

本质原因：检索 query 构造弱、检索通道单一、重排信号不足、失败后缺少补救与诊断。

### 2.2 chunk 粒度与上下文组织问题

对应问题：3、7、13、14、15、16、27、28、31、48、49。

主要症状：
- chunk 太大导致局部关键信息被淹没，太小又导致语义断裂。
- 同一文档重复 chunk 挤占上下文。
- 用户要步骤或代码修复，却注入概念介绍。
- 比较/归纳问题需要多证据覆盖，精确问答又需要唯一证据聚焦。

本质原因：当前上下文单元以“静态 chunk”为主，缺少面向任务的 snippet 抽取、邻域扩展与去重覆盖控制。

### 2.3 多轮会话与任务转向问题

对应问题：4、20、21、29、30。

主要症状：
- query 带指代或依赖前文隐含约束，检索却只看当前轮字面。
- 会话很长时，不知道历史、retrieval、工具结果如何分配预算。
- 用户任务已经转向，但系统延续了上一轮检索焦点。

本质原因：缺少显式的“查询构造层”和“会话目标漂移检测层”。

### 2.4 证据优先级与冲突仲裁问题

对应问题：5、11、12、22、36、37、38、39。

主要症状：
- 不同来源可信度不同，但排序未显式编码 source prior。
- 新旧知识冲突、知识库与工具冲突、知识库与用户显式输入冲突时，没有统一决策规则。
- memory、retrieval、tool、user-provided evidence 同时存在时，缺少证据层级。

本质原因：系统里有多个信息源，但没有统一证据治理模型。

### 2.5 生成约束与可审计性问题

对应问题：6、23、24、44、45、50。

主要症状：
- 模型即使拿到证据，仍可能超出证据补写。
- 引用了 chunk，但引用未真正支撑结论。
- 证据不足时，缺少标准化拒答、降置信或补充说明策略。
- 审计只看到“引用过”，看不到“引用是否支撑结论”。

本质原因：生成阶段缺少证据绑定约束，输出阶段缺少 citation/provenance 结构化设计。

### 2.6 任务类型与 purpose 差异化问题

对应问题：25、26、32、34、35、42、43。

主要症状：
- 问答、写作、代码、审阅任务共用一套 retrieval 注入策略。
- planning、reasoning、writing 不同 purpose 被迫共享同一批检索结果。
- 无法判断哪些问题应直接推理，哪些问题应优先调工具，哪些问题才适合 RAG。

本质原因：系统尚未建立 purpose-aware retrieval policy。

### 2.7 预算与准入控制问题

对应问题：27、32、33。

主要症状：
- token 预算紧张时，无法在“高分优先”和“覆盖优先”之间动态取舍。
- top-k 固定，不能随 query 难度、purpose、预算变化。
- rerank score 只是排序信号，不是准入门槛。

本质原因：缺少 retrieval budget controller 与 admission gate。

### 2.8 评估与失败分层问题

对应问题：46、47、51。

主要症状：
- 离线 Recall@K 看起来不错，但线上生成质量仍然差。
- 无法把失败区分为 query 问题、召回问题、重排问题、注入问题、生成越界问题。

本质原因：评估指标和日志埋点仍停留在粗粒度层面。

## 3. 总体优化原则

### 3.1 以“证据流”替代“检索结果列表”思维

优化目标不应只是返回 top-k chunk，而应构建一条完整证据流：
- 先判断当前任务是否需要 RAG。
- 再构造任务化 query。
- 再做多通道召回与重排。
- 再做证据去重、裁剪、扩展、仲裁。
- 最后将“可用证据对象”注入生成器，并要求输出绑定引用。

### 3.2 以 purpose 为主轴进行策略分流

至少应区分以下 purpose：
- fact_qa：要求答案被单条或少量高置信证据直接支撑。
- comparative_summary：要求覆盖多来源、多证据视角。
- procedural_howto：要求步骤性、操作性证据优先。
- code_fix：要求错误码、配置项、API、代码片段精确命中。
- planning_background：允许较宽背景材料，但不应污染执行事实。

### 3.3 以“准入”优于“排序”

很多问题不是“好内容排第几”，而是“不该进上下文的内容为什么进来了”。因此应建立：
- 最低相关性门槛。
- source trust 门槛。
- 新鲜度门槛。
- 去重与覆盖门槛。
- 与任务结构匹配度门槛。

### 3.4 以“冲突可见”优于“冲突静默融合”

面对知识冲突时，系统不应自动平滑融合，而应优先：
- 显示冲突来源。
- 按优先级仲裁。
- 无法仲裁时给出受限回答或请求澄清。

### 3.5 以“失败分层”驱动迭代

必须把失败拆成可定位环节，否则所有问题最后都会被记成“RAG 效果不好”。

## 4. 分阶段执行方案

## 4.1 第一阶段：建立检索治理骨架

目标：先把“是否检索、如何检索、如何记录失败”标准化，而不是立刻追求最强效果。

### 4.1.1 建立 retrieval decision 层

新增能力：在正式检索前，增加任务判别。

建议输出结构：
- `need_retrieval`
- `need_tools`
- `purpose`
- `freshness_required`
- `authority_required`
- `answer_mode`（strict_grounded / best_effort / refuse_if_insufficient）

落地建议：
- 在路由/编排层引入统一决策入口，优先关注 [`app/services/runtime_router.py`](../app/services/runtime_router.py)、[`app/services/mode_router.py`](../app/services/mode_router.py)、[`app/services/mission_routing.py`](../app/services/mission_routing.py) 一类文件。
- 将“是否需要 RAG”从 prompt 隐式判断升级为结构化字段，避免所有问题默认进检索。

### 4.1.2 建立 query construction 层

新增能力：将用户原始问题改写成“面向检索的查询对象”，而不是直接把原问题送进检索。

建议查询对象包含：
- `standalone_query`：消解指代后的独立 query。
- `must_have_terms`：专有名词、报错码、配置项、类名、接口名。
- `soft_terms`：同义词、改写表达。
- `task_constraints`：例如“需要步骤”“需要代码修复”“只要最新版本”。
- `time_scope`：latest / historical / unspecified。
- `source_scope`：规范、内部文档、讨论串、用户上传文件等。

落地建议：
- 可在现有上下文处理能力附近扩展，例如 [`app/services/prompt_context_gateway.py`](../app/services/prompt_context_gateway.py)、[`app/services/context_registry.py`](../app/services/context_compressor.py) 所在链路周边增加 query builder。
- 历史轮次中只抽取“当前 query 真正依赖的约束”，而不是整段会话拼接。

### 4.1.3 建立失败分类日志

新增失败标签：
- `no_recall`
- `bad_query`
- `low_rank_relevant`
- `wrong_granularity`
- `context_duplication`
- `stale_evidence`
- `evidence_conflict`
- `unsupported_generation`
- `fake_citation`
- `purpose_mismatch`

落地建议：
- 在检索、重排、生成后校验各阶段打日志。
- 关注 [`app/services/rag_metrics.py`](../app/services/rag_metrics.py)、[`app/services/rag_eval.py`](../app/services/rag_eval.py)、[`app/services/metrics_service.py`](../app/services/metrics_service.py) 一类模块扩展埋点结构。

阶段产出：
- 有统一 retrieval 决策。
- 有结构化 query 对象。
- 有失败分层日志。

## 4.2 第二阶段：提升召回正确性与排序质量

目标：解决“找不到、找不准、排不前”的核心问题。

### 4.2.1 使用多通道召回

建议至少并行组合：
- dense retrieval：解决语义改写。
- lexical retrieval：解决专有名词、报错码、参数名精确匹配。
- metadata filtering：解决时间范围、来源范围、domain 限制。

执行要点：
- 对含错误码、缩写、配置键名的 query，提高 lexical 权重。
- 对口语短 query，先改写再召回，不要直接裸搜。
- 对“最新信息”要求，先在召回层就加入 freshness filter，而不是等生成时再判断。

### 4.2.2 引入两级重排

建议流程：
1. 粗排：融合 dense score、BM25 score、metadata prior。
2. 精排：对候选集使用 cross-encoder 或任务化 rerank。

排序特征建议：
- 语义相关性。
- 关键词精确命中度。
- source authority prior。
- recency prior。
- task match prior（步骤类/代码类/定义类）。
- novelty/diversity signal。

策略建议：
- cross-encoder 用于高价值、需要精确排序的复杂 query。
- lexical rerank 适用于关键词强约束、低歧义问题。
- 不要只保留单一总分，保留分项得分以便后续诊断。

### 4.2.3 将 rerank score 升级为 admission gate

建议规则：
- 分数低于门槛时，不进入上下文。
- 若全部低于门槛，则进入改写重试或返回“证据不足”。
- 对不同 purpose 配置不同阈值：
  - fact_qa：高阈值。
  - comparative_summary：允许更宽覆盖。
  - planning_background：允许中等阈值。

阶段产出：
- 召回噪音下降。
- 高相关 chunk 排位前移。
- 低质量证据不再无条件注入。

## 4.3 第三阶段：重做 chunk 注入单元

目标：把“注入什么”从静态 chunk 升级成任务适配证据对象。

### 4.3.1 建立 snippet-first 注入策略

注入单元建议由以下字段组成：
- `snippet_text`
- `source_id`
- `chunk_id`
- `char_range` 或 `line_range`
- `support_type`（direct / contextual / comparative）
- `score_breakdown`

核心思想：
- 默认优先注入 snippet，而不是整段 chunk。
- 仅在需要上下文解释时再附带邻接窗口。

### 4.3.2 建立受控邻域扩展

当 snippet 语义不完整时，可按规则扩展：
- 先向前后各扩 1 个最小单元。
- 若扩展后引入噪音比例过高，则回退。
- 扩展预算受总 token 上限控制。

这能对应解决 [`question.log`](../question.log) 中第 15、16 条的上下文断裂与扩展噪音问题。

### 4.3.3 建立去重与覆盖平衡器

建议规则：
- 同一文档最多保留 N 条高重合证据。
- 比较/总结类任务要求最少跨 M 个来源。
- 精确问答任务要求优先集中到最强支撑证据，避免多证据引入歧义。

### 4.3.4 引入任务结构匹配

针对不同任务类型定义不同注入偏好：
- 若用户要“步骤”，优先包含 numbered steps、操作指令、命令片段。
- 若用户要“代码修复”，优先包含 error trace、配置片段、API usage、patch 示例。
- 若用户要“定义/解释”，优先包含规范性描述而非论坛讨论。

阶段产出：
- 上下文利用率提高。
- 证据更贴近回答结构。
- 重复内容显著下降。

## 4.4 第四阶段：建立证据优先级与冲突仲裁机制

目标：解决“该信谁”的问题。

### 4.4.1 定义统一证据层级

建议默认优先级：
1. 用户当前轮显式提供内容。
2. 当前会话工具实时结果。
3. 用户新上传文件。
4. 高权威、最新规范文档。
5. 一般知识库文档。
6. memory/history 摘要。
7. 低权威讨论性文本。

此优先级可按场景调整，但必须显式存在。

### 4.4.2 定义冲突仲裁规则

建议规则：
- 用户显式输入与知识库冲突时：优先采纳用户输入，并标注与库冲突。
- 工具实时结果与旧知识冲突时：优先工具结果。
- 新上传文件与旧库冲突时：优先新文件。
- 高权威规范与低权威讨论冲突时：优先规范。
- 同级冲突且无法判定时：输出冲突说明，不强行融合。

### 4.4.3 将 authority 与 freshness 纳入排序和回答模板

不仅排序使用 prior，生成器也应拿到：
- 来源类型。
- 时间戳。
- 权威级别。
- 是否存在冲突证据。

这样可以避免模型只看文本内容，不看证据位阶。

阶段产出：
- 冲突处理从隐式变显式。
- 新旧内容、工具结果、用户内容之间有稳定裁决逻辑。

## 4.5 第五阶段：生成约束、引用与审计闭环

目标：保证答案真正被证据支撑。

### 4.5.1 定义 answer mode

建议最少支持三种模式：
- `strict_grounded`：只能基于证据回答，缺证据则拒答或说明不足。
- `best_effort_grounded`：优先基于证据，允许有限推断，但需标注推断部分。
- `refuse_if_insufficient`：只要关键证据不足就不继续回答。

### 4.5.2 引入 claim-to-evidence 绑定

输出前做简单校验：
- 每个关键结论必须至少绑定 1 条 direct evidence。
- 若只有 contextual evidence，不允许表述成确定事实。
- 若引用存在但并不支撑结论，标记为 fake citation。

### 4.5.3 设计最小可行 citation/provenance

最小字段建议：
- `claim_id`
- `citation_id`
- `source_title`
- `source_type`
- `source_timestamp`
- `chunk_id`
- `snippet_range`
- `support_strength`

这套结构既可用于前端展示，也可用于离线审计。

### 4.5.4 对证据不足建立标准回答策略

建议区分：
- 完全无证据：直接说明未找到足够依据。
- 有部分证据：给出受限回答，并明确缺口。
- 有冲突证据：列出冲突点与优先采信依据。

阶段产出：
- 幻觉式补写下降。
- 引用从“装饰品”变成“支撑结构”。
- 审计能力具备最小闭环。

## 4.6 第六阶段：评估体系升级

目标：让线上问题可以定位到具体环节。

### 4.6.1 离线指标升级

除 Recall@K 外，建议增加：
- MRR / nDCG：看相关证据排序是否靠前。
- Evidence Precision@Budget：进入上下文的证据中有多少真正相关。
- Coverage@Intent：多子问题是否都被证据覆盖。
- Diversity@Context：是否被单文档重复内容占满。
- Freshness Accuracy：最新需求场景下是否取到最新证据。
- Authority Accuracy：高权威内容是否优先。
- Grounded Answer Rate：答案关键结论是否均有证据支撑。
- Citation Support Rate：引用是否真实支撑结论。

### 4.6.2 线上日志升级

每轮建议记录：
- 原始 query。
- 改写 query。
- 召回候选与各自分项得分。
- 被过滤原因。
- 最终注入证据对象。
- answer mode。
- 是否触发冲突仲裁。
- 是否触发拒答/降置信。
- 最终失败标签。

### 4.6.3 建立失败归因面板

建议按以下链路查看失败：
- Query construction failure
- Recall failure
- Rerank failure
- Admission failure
- Context assembly failure
- Generation grounding failure
- Citation validation failure

阶段产出：
- 离线评测与线上表现建立映射。
- 失败能够定位到具体环节，而非泛化归因。

## 5. 唯一长期执行方案

本文不再区分初版、过渡版或最小闭环，而是直接给出目标态方案。后续实施应始终围绕同一终态架构推进，所有阶段性工作都只是向该唯一方案收敛，而不是形成多套并存策略。

### 5.1 目标态架构

长期方案的目标不是“增加几个 retrieval 技巧”，而是建设一条完整的 evidence operating system，包含 8 个稳定层次：

1. **Task Intent Layer**：识别当前请求属于哪类任务、是否需要 RAG、是否更适合工具、是否要求最新/高权威/严格证据回答。
2. **Query Construction Layer**：将用户问题、历史约束、实体名、must-have terms、时间与来源约束统一编译为 `QueryObject`。
3. **Multi-Retrieval Layer**：并行执行 dense、lexical、metadata-aware、domain-aware retrieval。
4. **Rerank & Admission Layer**：统一融合相关性、权威性、新鲜度、任务结构匹配度，并执行强准入控制。
5. **Evidence Assembly Layer**：生成 snippet-first 的 `EvidencePacket`，完成去重、覆盖平衡、邻域扩展、冲突标记。
6. **Grounded Generation Layer**：生成器严格消费证据对象与 answer mode，不再直接消费原始粗粒度 chunk 列表。
7. **Citation & Verification Layer**：对 claims、citation、support relation 做结构化校验。
8. **Observability & Evaluation Layer**：对 query、candidate、gate、assembly、grounding、citation 全链路可观测，并沉淀长期评测资产。

### 5.2 长期方案下的核心原则

- 不存在“裸检索结果直接进 prompt”的路径，所有检索结果必须经过统一准入与证据组装。
- 不存在“memory、retrieval、tool result 混成同类文本”的路径，必须统一纳入证据层级模型。
- 不存在“答案有引用就算 grounded”的判断，必须检查 claim 与 evidence 的支撑关系。
- 不存在“RAG 一刀切”的策略，必须按 purpose、task shape、budget、freshness、authority 要求进行动态分流。
- 不存在“Recall@K 好就表示系统好”的评估方法，必须看最终 evidence quality 与 grounded answer quality。

### 5.3 长期方案的实施路线

虽然只有一个长期目标态，但实施上仍要按依赖关系推进，顺序如下：

1. 先稳定数据结构与状态流。
2. 再稳定 retrieval decision 与 query construction。
3. 再统一多通道召回、重排与 admission。
4. 再升级 evidence assembly。
5. 再把 grounded generation 与 citation verification 串成闭环。
6. 最后补齐长期评测、灰度、回放与运营监控体系。

这里的“顺序”只是工程依赖顺序，不代表存在独立的短期方案；所有工作都必须一次性对齐目标态字段、接口与观测结构，避免后续返工。

## 6. 与现有系统的结合建议

结合当前仓库结构，建议优先关注以下落点：

### 6.1 服务层

- [`app/services/prompt_context_gateway.py`](../app/services/prompt_context_gateway.py)：适合作为上下文准入、注入编排的核心入口。
- [`app/services/context_meter.py`](../app/services/context_meter.py)：适合承接 budget 控制与注入预算分配。
- [`app/services/resource_budget.py`](../app/services/resource_budget.py)：适合承接 top-k、snippet 长度、扩展窗口等资源上限策略。
- [`app/services/rag_eval.py`](../app/services/rag_eval.py) 与 [`app/services/rag_metrics.py`](../app/services/rag_metrics.py)：适合扩展离线/线上指标与失败分层。
- [`app/services/relevance_gate.py`](../app/services/relevance_gate.py)：适合作为 admission gate 的落点。
- [`app/services/runtime_router.py`](../app/services/runtime_router.py) 与 [`app/services/mode_router.py`](../app/services/mode_router.py)：适合接入 retrieval decision 与 purpose 分流。

### 6.2 节点层

- [`app/nodes/retrieval_node.py`](../app/nodes/retrieval_node.py)：适合接入 query object、多通道召回、候选日志。
- [`app/nodes/reasoning_node.py`](../app/nodes/reasoning_node.py)：适合接入 answer mode 与 grounded generation 约束。
- [`app/nodes/output_guard_node.py`](../app/nodes/output_guard_node.py)：适合接入 citation 校验、fake citation 检查。
- [`app/nodes/planning_node.py`](../app/nodes/planning_node.py) 与 [`app/nodes/writing_node.py`](../app/nodes/writing_node.py)：适合接入 purpose-aware retrieval policy，避免 planning/writing 共用同一上下文装配模式。

## 7. 工程实施细节与实施技巧

本节聚焦“如何把方案真正做出来”，强调模块边界、增量接入方式、灰度技巧与工程落地顺序。

### 7.1 建议的数据结构先行

在真正修改检索链路前，建议先定义统一的数据结构，避免后续能力继续堆在匿名字典和 prompt 文本中。

建议优先引入以下对象：
- `RetrievalDecision`：描述是否检索、purpose、answer mode、freshness/authority 要求。
- `QueryObject`：描述 standalone query、must-have terms、soft terms、constraints、source/time scope。
- `CandidateEvidence`：描述召回候选及其 dense、lexical、authority、freshness、task-match 分项分数。
- `EvidencePacket`：描述真正注入模型的 snippet、range、support type、source metadata。
- `EvidenceConflict`：描述冲突双方、优先级、仲裁结果、仲裁理由。
- `GroundingCheckResult`：描述 claim 是否被 direct evidence 支撑、是否存在 fake citation。

工程技巧：
- 优先在 [`app/runtime/state_models.py`](../app/runtime/state_models.py)、[`app/runtime/agent_state_model.py`](../app/runtime/agent_state_model.py) 或相邻状态定义层补齐字段，避免业务服务各自定义一套结构。
- 字段命名保持“可观测”和“可序列化”，方便后续直接输出到日志、评测与 API。
- 每个对象尽量保留 `debug_info` 或 `score_breakdown` 字段，避免后续诊断时只能看到总分。

### 7.2 采用“包裹式接入”而非“推倒重写”

建议不要直接重写现有 retrieval 主链路，而是在关键点增加 wrapper/adapter。

推荐接入方式：
1. 在路由层先产出 `RetrievalDecision`。
2. 在检索入口前新增 query builder，把原始 query 包装为 `QueryObject`。
3. 让现有召回器继续工作，但输出统一转成 `CandidateEvidence`。
4. 在进入 prompt 前增加独立 admission gate，把 `CandidateEvidence` 转成 `EvidencePacket`。
5. 在输出阶段增加 grounded 校验，不直接侵入生成主逻辑。

工程技巧：
- 优先在 [`app/services/prompt_context_gateway.py`](../app/services/prompt_context_gateway.py)、[`app/services/relevance_gate.py`](../app/services/relevance_gate.py)、[`app/nodes/retrieval_node.py`](../app/nodes/retrieval_node.py)、[`app/nodes/output_guard_node.py`](../app/nodes/output_guard_node.py) 增加中间层，而不是大面积改动已有 prompt 拼装逻辑。
- 新老链路可通过配置并存，例如在 [`config/config.yaml`](../config/config.yaml) 增加 `retrieval.policy_version`、`retrieval.enable_admission_gate`、`retrieval.enable_snippet_first` 一类开关。
- 先保证默认配置下行为不变，再逐步打开能力开关。

### 7.3 建议的配置化项

为避免策略写死在代码里，建议配置化以下参数：
- purpose 到 top-k、budget、rerank threshold 的映射。
- source type 到 authority prior 的映射。
- freshness decay 或 latest-only 过滤规则。
- 同源文档最大注入条数。
- snippet 最大长度、邻域扩展窗口、最大扩展轮数。
- query rewrite 最大重试次数。
- citation 校验开关与严格级别。

工程技巧：
- 配置项先放在 [`config/config.yaml`](../config/config.yaml) 与 [`config/config.docker.yaml`](../config/config.docker.yaml)，避免散落在多个服务常量中。
- 对阈值类配置给出注释和默认值，不要只暴露裸数字。
- 为不同环境保留独立配置，例如 dev 环境记录更详细 debug 字段，prod 环境限制日志体积。

### 7.4 第一阶段实施细节

#### 7.4.1 retrieval decision 的接入技巧

实施步骤：
1. 在请求进入主编排链路时，根据任务类型、用户输入、工具需求构建 `RetrievalDecision`。
2. 让后续节点只消费该对象，而不是再次从 prompt 文本反推是否需要检索。
3. 当 `need_retrieval=false` 时，明确记录“跳过检索”的原因。

实施技巧：
- 初版 purpose 分类不要过细，先保留 4 到 6 类即可。
- 对不确定分类的请求，允许回落到 `general_grounded`，不要因为分类失败阻断主流程。
- 将 decision 结果打到结构化日志中，便于分析“哪些请求其实不需要 RAG”。

#### 7.4.2 query construction 的接入技巧

实施步骤：
1. 从当前轮提取显式问题主体。
2. 从历史轮次提取有限约束，如对象、时间范围、输出格式。
3. 抽取 must-have terms。
4. 对短 query、口语 query 或指代 query 进行 standalone rewrite。
5. 保留 rewrite 前后两个版本，供评估比较。

实施技巧：
- 一定保留原始 query，不要只存改写结果，否则后续很难诊断 rewrite 是否引入偏差。
- rewrite 不应追求“语言更优美”，而应追求“检索约束更明确”。
- 历史提取建议做白名单，只继承任务目标、实体名、时间条件，不要全量拼接上下文。

#### 7.4.3 失败分类日志的接入技巧

实施步骤：
1. 在召回后记录候选数量、来源分布、基础分数。
2. 在 rerank 后记录 relevant item 的名次变化。
3. 在 admission 后记录被过滤原因。
4. 在输出后记录 grounding/citation 校验结果。
5. 汇总生成单轮 failure taxonomy。

实施技巧：
- 一轮请求允许多个 failure tag，不要强制只有一个根因。
- “无结果”与“结果被全部 gate 掉”必须区分记录。
- 对日志字段做长度裁剪，避免把整段 chunk 全量打进生产日志。

### 7.5 第二阶段实施细节

#### 7.5.1 多通道召回的工程落地

实施步骤：
1. 保留现有主召回通道作为 baseline。
2. 新增 lexical 或 metadata filter 通道。
3. 将多通道结果按 `candidate_id` 去重合并。
4. 为每条候选保留各通道分数，不立即压成单总分。

实施技巧：
- 合并阶段不要只按文本去重，更应优先按文档 ID、chunk ID、规范化 hash 去重。
- 对代码、报错码、配置项场景，可先 short-circuit 到 lexical-heavy 策略。
- metadata filter 放前面通常比放后面更省预算。

#### 7.5.2 两级重排的工程落地

实施步骤：
1. 粗排保留较大候选集，例如 top 30~100。
2. 精排只对有限候选集运行，以控制时延与成本。
3. 输出分项得分和最终 rank。

实施技巧：
- cross-encoder 不要全量启用，应只在高复杂度 query 或高价值场景触发。
- 对超长 chunk 先截取候选局部片段再做精排，否则分数会被无关上下文稀释。
- 为 rerank 设置超时与 fallback，超时后回退粗排结果，避免拖垮主链路。

#### 7.5.3 admission gate 的工程落地

实施步骤：
1. 先按 purpose 设定最低阈值。
2. 再叠加 authority/freshness/duplication 过滤。
3. 记录每条候选被过滤的具体原因。
4. 若全部被过滤，进入 rewrite retry 或证据不足分支。

实施技巧：
- admission gate 初期建议只做“软拦截 + 打日志”，确认效果后再切到强拦截。
- 不要用单一总分决定一切，某些 query 对 must-have term 命中比语义分更重要。
- 允许 purpose 覆盖默认阈值，例如 summary 任务应容忍更宽覆盖。

### 7.6 第三阶段实施细节

#### 7.6.1 snippet-first 的工程落地

实施步骤：
1. 对候选 chunk 先做局部句段切分。
2. 根据 query 与 purpose 识别最有支撑力的句段。
3. 将句段封装成 `EvidencePacket`，附带 source metadata 与 range。
4. 仅在必要时拼接局部邻域。

实施技巧：
- sentence splitter 要兼容中英文、列表、代码块、表格行，否则容易切坏证据边界。
- 对代码类证据，建议最小单元使用“代码块/函数片段/错误上下文块”，不要强行按自然语言分句。
- 如果 snippet 抽取质量不稳定，可先保留 `snippet + 原 chunk 摘要` 双轨输出进行观察。

#### 7.6.2 去重与覆盖控制的工程落地

实施步骤：
1. 对候选按 source_id 分桶。
2. 每桶内做相似度去重。
3. 按任务要求决定是“集中最强证据”还是“跨源覆盖”。
4. 在 token 预算内组装最终上下文。

实施技巧：
- 去重相似度建议同时参考 embedding 与文本 overlap，单一方法容易误杀。
- 比较/总结类任务应设置最小 source diversity 约束。
- 精确查找类任务应避免“为了多样性而多样性”。

### 7.7 第四与第五阶段实施细节

#### 7.7.1 冲突仲裁的工程落地

实施步骤：
1. 为每条证据打上 `source_type`、`authority_level`、`timestamp`。
2. 在组装上下文前做冲突检测。
3. 输出仲裁结果或冲突说明。
4. 将冲突状态显式传给生成器。

实施技巧：
- 初版冲突检测不必追求复杂 NLI，可先做规则法：同一字段、同一实体、不同值。
- 对工具结果与知识库冲突，建议直接产生 `conflict_alert` 而不是静默覆盖。
- 当冲突无法自动裁决时，生成器模板必须转入“受限回答”模式。

#### 7.7.2 grounded generation 的工程落地

实施步骤：
1. 生成前把 `EvidencePacket` 与 answer mode 一并注入。
2. 生成后抽取关键 claims。
3. 对 claims 做 citation/support 校验。
4. 不通过时触发重写、降置信或拒答。

实施技巧：
- claim 抽取不需要一开始就做到完美，可以先只覆盖结论句、步骤结论、修复建议三类高价值 claim。
- citation 校验先覆盖“有没有引用”“引用是否来自已注入 evidence”，再逐步升级到“是否真正支撑结论”。
- output guard 不应改写业务内容过多，应优先给出失败原因和回退策略。

## 8. 陷阱避免清单

### 8.1 常见实施陷阱

- 只加 rewrite，不做回放评测，结果 query 变漂亮但召回更差。
- 只看 Recall@K，不看最终注入内容，导致“召回正确、注入错误”。
- 只做排序，不做 gate，导致低分噪音仍进入 prompt。
- 只做 source prior，不保留分项分数，后续无法解释为什么某条被顶上去。
- 只做 citation 展示，不做 citation 支撑校验，形成“伪可审计”。
- 将 memory 当知识证据使用，导致历史偏好污染事实回答。
- 将比较/总结任务按单证据问答优化，导致覆盖不足。
- 将精确查找任务按多样性优化，导致唯一答案被稀释。

### 8.2 性能与稳定性陷阱

- cross-encoder 全量开启，导致时延暴涨。
- snippet 抽取和 citation 校验同步串行执行，导致链路过长。
- 日志记录过重，把大段 chunk、历史会话、工具输出全量写入日志。
- 在 prod 环境直接打开强 gate，没有灰度与回放验证，导致命中率骤降。
- 阈值配置缺乏环境隔离，开发调参误伤生产。

### 8.3 组织协作陷阱

- 检索、生成、评测三方各自定义指标，结果无法闭环。
- 研发只追求离线分数，忽略线上真实 query 分布。
- 缺少失败 case 池，导致每次优化都在重复讨论主观感受。

## 9. 测试与验证方案

### 9.1 测试分层

建议至少分成 5 层：
1. 单元测试：验证 decision、rewrite、gate、dedup、citation check 等纯逻辑。
2. 组件测试：验证 retrieval pipeline、rerank、snippet 抽取、context assembly。
3. 回放测试：使用真实线上 query 样本回放，比较新旧策略差异。
4. 端到端测试：验证最终答案质量、引用、拒答、冲突说明。
5. 灰度验证：在线上小流量比较关键指标变化。

### 9.2 单元测试建议

建议优先覆盖：
- `RetrievalDecision` 分类是否符合预期。
- `QueryObject` 是否正确保留 must-have terms 与历史约束。
- admission gate 是否能正确过滤低相关、低权威、过期、重复证据。
- snippet 抽取是否保留正确 range。
- 冲突仲裁规则是否符合优先级定义。
- citation 校验是否能识别无引用、错引用、假引用。

测试技巧：
- 对纯规则逻辑使用表驱动测试。
- 对阈值判断增加边界值测试。
- 为每类 failure taxonomy 准备最小样例，防止日志标签回归。

### 9.3 回放测试建议

建议构建回放样本集，覆盖以下类型：
- 短 query / 口语 query。
- 指代型多轮 query。
- 专有名词 / 报错码 / 配置项 query。
- 最新性敏感 query。
- 冲突证据 query。
- 比较/总结型 query。
- 精确唯一答案型 query。
- 代码修复型 query。

验证维度建议：
- relevant evidence rank 是否前移。
- 注入上下文中的噪音是否减少。
- 关键子问题覆盖率是否提升。
- grounded answer rate 是否提升。
- refusal/limited answer 是否更合理。

### 9.4 端到端验收标准

建议定义最小验收标准：
- 不需要 RAG 的请求，误触发检索率下降。
- 需要 RAG 的请求，relevant evidence 进入上下文的比例提升。
- 最新性/高权威场景下，旧证据误用率下降。
- 比较型任务的多源覆盖率提升。
- 精确查找型任务的歧义答案率下降。
- 引用答案中，citation support rate 达到预设目标。
- 证据不足场景中，盲答率下降。

### 9.5 灰度与 A/B 验证技巧

建议灰度顺序：
1. 先只开日志与影子评估，不改线上行为。
2. 再开启软 gate，只记录如果强拦截会发生什么。
3. 再对低风险 purpose 开启新策略。
4. 最后扩大到高价值与高复杂度场景。

A/B 关注指标建议：
- 首 token 时延与总耗时。
- 检索命中率与 admission 通过率。
- grounded answer rate。
- citation support rate。
- 用户追问率、纠错率、人工介入率。
- 因强 gate 导致的空回答率。

### 9.6 测试资产建设建议

建议沉淀以下资产：
- 一个持续扩充的 failure case 库。
- 一组 purpose 标注数据。
- 一组 evidence relevance 与 citation support 标注数据。
- 一套可重复执行的 query replay 脚本。
- 一张失败归因 dashboard。

## 10. 风险与注意事项

### 10.1 不要一次性引入过多复杂度

如果在没有观测数据的情况下同时上 query rewrite、fusion、cross-encoder、snippet 抽取、citation 校验，系统会很难调试。应坚持“小步上线、逐层观测”。

### 10.2 不要把所有问题都交给 prompt 解决

[`question.log`](../question.log) 中的大部分问题，本质都属于系统策略与数据流问题，而不是单纯 prompt 文案问题。Prompt 只能约束生成器，不能替代准入、裁剪、冲突治理与评估。

### 10.3 不要让 memory 与 retrieval 混成同一层证据

memory 更适合补用户偏好、历史目标、上文约束；retrieval 更适合补外部知识。两者应在数据结构和优先级上明确分层。

## 11. 结论

[`question.log`](../question.log) 反映出的不是单点检索问题，而是一个完整的“证据治理”问题。优化方向应从传统 RAG 的“召回更多内容”，升级为“按任务组织可信证据、限制无关上下文进入、让答案对证据负责”。

最优执行路径是：
- 先建立 retrieval decision、query construction、admission gate、失败分层日志。
- 再升级多通道召回、重排、snippet-first 注入、覆盖去重控制。
- 最后补齐冲突仲裁、claim-to-evidence 绑定与 citation 审计闭环。

新增的工程实施细节、实施技巧、陷阱避免清单与测试验证方案，可直接用于后续技术方案拆分、开发排期、验收标准制定与灰度发布控制。

按该顺序推进，可以在不大幅扰动现有系统的前提下，逐步提升检索质量、生成可信度与线上可运营性。
