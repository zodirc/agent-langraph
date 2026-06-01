# 长文手稿（Manuscript）子系统

> 版本：1.3 · 状态：已实现（Writing 节点 + 手稿状态 + Steer 双阶段确认 + execution grant + work-plan reconcile）  
> 开发规范：[`DEVELOPMENT_GUIDELINES.md`](../DEVELOPMENT_GUIDELINES.md) · 图编排：[`app/runtime/graph.py`](../app/runtime/graph.py)

## 1. 目标

将「万字小说 / 长报告」从 **规划里塞正文 + 工具节点落占位符** 升级为 **可长期演进的手稿子系统**：

- 会话级 **单一手稿**（Manuscript），不靠扫目录猜文件名
- **规划只输出意图**（WritingIntent），不携带 `tool_params.content` 正文
- **Writing 节点** 专职生成、校验、写入 artifact
- **Reasoning 节点** 只总结「已写入事实」，不承诺未执行的写作
- 领域规则放在 **配置 + 领域包**，避免业务正则散落在各节点

## 2. 非目标（本阶段不做）

- 多手稿并行（一篇 session 一个 `manuscript_id`）
- 推理 JSON 流式直写磁盘（P1，见主架构 §27.2）
- 自动合并历史重复文件（仅绑定 canonical 路径，不删旧文件）

## 3. 状态模型

### 3.1 `AgentState.manuscript`

运行时单一真相源（与磁盘同步，每轮 Writing 后刷新）：

```python
{
  "body_path": "novel.txt",           # 正文 artifact 文件名
  "outline_path": "outline.txt",      # 大纲文件名（可选）
  "body_bytes": 58284,
  "outline_bytes": 14768,
  "outline_revision": 3,             # 大纲修订号（每次 write_outline 成功 +1）
  "body_revision": 1,                # 正文修订号（每次 reset_body 成功 +1）
  "body_outline_revision_seen": 3,   # 当前正文最后一次对齐的大纲版本
  "session_turn": 11,
  "updated_at": "ISO-8601"
}
```

解析规则见 `ManuscriptService.resolve()`：优先 state 中路径，否则从 task artifact 目录推断（体积最大正文 + 大纲标记）。

### 3.2 `input_payload.writing_intent`

规划 LLM 输出（或 `extract_writing_intent()` 从工具列表推导）：

```python
{
  "enabled": true,
  "action": "append_body",    # write_outline | write_body | append_body
  "target_chars": 3500,       # 本轮目标字数
  "min_chars": 200,           # 校验下限
  "chapter_label": "第81-92章",
  "source": "planning"
}
```

**禁止**在 `tool_params.write_text_artifact.content` / `append_text_artifact.content` 中放正文或占位符；该字段若出现，Planning 剥离后仅保留 `filename`（可选）。

### 3.3 会话记忆

| 层级 | 内容 |
|------|------|
| `conversation_history` | 多轮问答（[`conversation_context.py`](../app/services/conversation_context.py) 统一读写 state + payload） |
| `memory_hits` | 多轮时 `retrieval_policy` + `session.memory_retrieval_enabled` 检索 episode / session_summary |
| `previous_artifact_excerpt` | 正文尾部摘录（供 Writing LLM） |
| `memory_store` | 回合摘要 + `manuscript` 快照（PG 表 `memories.session_id`） |
| `manuscript` | 路径与字节数（供规划/推理引用） |

## 4. 执行图（成熟路径）

```text
planning → retrieval? → tool_execution* → writing? → reasoning → policy → output → memory_writeback

* tool_execution：仅非写作工具（read、calculator、get_runtime_info…）
* writing：当 writing_intent.enabled 为 true
```

与旧路径对比：

| 旧路径 | 问题 | 新路径 |
|--------|------|--------|
| planning 内 `prefill_writing_tool_params` | 规划阶段阻塞、易写入占位符 | 仅生成 `writing_intent` |
| tool 执行 write/append | 先于 reasoning，且可能 append 占位符 | 写作工具移至 Writing 节点 |
| reasoning 输出 30k JSON | 不落盘，与用户感知不一致 | reasoning 只读 `tool_results` |

## 5. 节点职责

### Planning

- 注入 `manuscript` 与 `previous_artifact_excerpt`
- LLM 返回 `plan`、`selected_tools`、`writing_intent`（推荐显式）
- `split_execution_tools()`：`write/append` → writing_intent，其余进 `selected_tools`
- **不调用** `prefill_writing_tool_params`

### Tool execution

- **跳过** `write_text_artifact` / `append_text_artifact`
- 可执行 `read_text_artifact`（读大纲/正文）

### Writing（新）

1. 读取 `writing_intent` + `manuscript`
2. 调用 `generate_artifact_content()`（写作 LLM）
3. `validate_manuscript_content()` — 拒绝占位符、过短内容
4. `handle_write_text_artifact` / `handle_append_text_artifact`
5. 刷新 `manuscript`，合并 `tool_results`
6. `status = WRITTEN`

#### 纲-文对齐（Outline→Body alignment）

当本轮 `writing_intent.action == "write_outline"` 且已存在正文（`novel.txt` 非空）时，Writing 节点会在写入新大纲后做一次结构化判定：

- 输入：旧大纲摘录（`existing_outline_excerpt`）、新大纲摘录（本轮生成）、正文尾部摘录
- 输出：`outline_body_alignment = {change_level, body_action, reason}`
- 若判定 `body_action == "rewrite_body"`：写入 **forced** `mission_intervention.reset_body`，使 mission 下一步优先重写正文而不是从旧尾部续写。

目的：避免「推翻大纲但正文继续从旧位置 append」导致的不一致；同时保留轻量改纲时继续续写的能力（非硬编码）。

### Reasoning

- 以 `tool_results` 与 `manuscript` 为 ground truth
- 禁止在 summary 中声称「将要写入」而未在 tool_results 出现的字节数
- 输出协议：reasoning 必须返回裸 JSON；若需要展示代码块，放入 `summary` 字符串而非 JSON 外
- 解析治理：`extract_json_with_repair` 先尝试 JSON 提取，再做 reasoning 专用 repair；仍失败才进入结构化 fallback
- 可观测：审计会记录 `parser_repaired` / `parser_fallback`，并上报 `reasoning_parser_*` 指标
- Policy：`parser_fallback` / `parser_repaired` 视为格式恢复，不因低 `confidence` 强制人工审核（见 `policy_engine`）
- 代码输出：使用 `structured.artifacts[]`（`kind=code`）保存源码；[`answer_compose.py`](../app/services/answer_compose.py) 组装最终回答
- Output guard：[`content_segments.py`](../app/services/content_segments.py) 仅扫描 prose，代码块内长数字不触发 PII 误杀

### 多轮推理隔离（问答 vs 写作后结案）

- 模块：[`reasoning_shortcut.py`](../app/services/reasoning_shortcut.py) · 文档：[`REASONING_SHORTCUT.md`](REASONING_SHORTCUT.md)
- 每轮新 user 消息清除上一轮「写完即结案」；问答/回顾强制完整推理，避免只报手稿字节数。
- 与 Writing 节点配合：仅**本轮**执行过写作时，执行清单才可包含手稿信息。

### Route audit（规划 vs 执行路径）

- 模块：[`app/services/route_audit/`](../app/services/route_audit/) · 文档：[`ROUTE_AUDIT.md`](ROUTE_AUDIT.md)
- **手稿**（`novel.txt` / mission writing）与 **源码**（C++/Python 等）由配置化 `kinds` + `conflicts` 区分，不靠用户句硬编码。
- 误将代码任务路由到 `write_body` → 关闭 `writing_intent`，强制走 reasoning + `structured.artifacts`。
- `writing_node` 与 router 使用 `writing_gate_allowed()`；artifact 生成按 `artifact_profile` 选择提示（`source_code` 不用「Story prose only」）。

### 多轮 QA 会话上下文（与 Mission 共用）

- 模块：[`conversation_context.py`](../app/services/conversation_context.py)（history 合并、turn 结束写回、草稿持久化）
- 每轮 `finalize_turn_history` 将 user + assistant 写入 `state.conversation_history` 与 `input_payload.conversation_history`
- 人工审核中断前：`graph_runner` 调用 `persist_turn_draft_answer`，避免「流式已展示但未入库」
- 多轮记忆：`retrieval_policy.needs_session_memory_retrieval`；配置 `session.memory_retrieval_enabled`

详见 [`conversation_context.py`](../app/services/conversation_context.py) 与 [`session_turn.py`](../app/services/session_turn.py)。

## 6. 内容校验（ContentValidator）

配置：`config.yaml` → `manuscript.min_body_chars`、`placeholder_patterns`

默认拒绝：

- 含 `待续写`、`待填充`、`本回合内容` 等
- 过短正文（append 默认 ≥200 字，可 per-intent 覆盖）
- 与 goal 完全相同且 goal 极短

校验失败：Writing 节点重试一次生成；仍失败则 `WRITING_FAILED` 并记入 audit。

## 7. 配置

```yaml
manuscript:
  default_body: novel.txt
  default_outline: outline.txt
  min_body_chars: 200
  min_outline_chars: 80
  tail_excerpt_chars: 2400
  placeholder_patterns:
    - 待续写
    - 待填充
    - 本回合内容
    - 占位
```

## 8. 扩展路线（领域包）

后续将 `ManuscriptPolicy` 迁入 `app/domain/packs/document.py`：

- 体裁模板（小说 / 报告 / 剧本）
- 章节拆分策略
- 文件名约定

Runtime 只依赖 `ManuscriptService` 接口，不依赖中文正则。

## 9. 迁移与兼容

- `artifact_session.py` 保留为薄封装，委托 `manuscript_service`
- 旧会话：首轮 `resolve()` 从磁盘绑定 `novel.txt` 或最大正文文件
- API / SSE / Web CLI 无破坏性变更

## 10. 验收标准

1. 续写回合 `novel.txt` 增长 ≥ `min_body_chars`，尾部无占位符
2. 同 session 不新增平行正文文件（除非首回合 `write_body`）
3. `reasoning.summary` 与 `tool_results` 字节数一致
4. `manuscript` 在 state_store 跨轮持久化

## 11. 长任务编排（Orchestration）与人工 Steer

**原则**：不用正则猜用户意图；提供 **工具 + 显式强制介入（forced intervention）**。

### 编排（短任务从哪来）

| 来源 | 说明 |
|------|------|
| **懒加载**（默认） | `work_plan.mode=lazy`，每步由 `step_policy` 生成一个工作项；`autonomous` 时在单轮内连续执行，`interactive`+`stepwise` 时每步 `MISSION_PAUSED`（`pause_reason=step_checkpoint`）；继续执行需 **execution grant**（见 [`MISSION_EXECUTION_CONTROL.md`](MISSION_EXECUTION_CONTROL.md)） |
| **agenda / DAG 扩展** | 规划产出的 `plan[]` 与 `tool_dag` 可以进一步投影为 dependency-aware agenda：见 [`items_from_plan_steps()`](../app/services/task_agenda.py:148)、[`merge_agenda_into_work_plan()`](../app/services/task_agenda.py:210)、[`select_next_runnable_item()`](../app/services/task_agenda.py:69)。这意味着 mission 内部已不再只支持线性顺序队列，而开始支持依赖、阻塞、局部重规划。 |
| **写作阶段（模型自选）** | `mission.writing_llm_decide=true` 时，每步 `mission_decide` 选择 `writing_phase`（写/审/润色/摘要/一致性/卷检查点），非固定流水线 |

### 写作阶段（`writing_phase`）

由 **mission_decide LLM** 在 `params.writing_phase` 中指定（见 `MISSION_WRITING_DECIDE_ROLE`），`mission_act` 映射为 `writing_intent`：

| 阶段 | 作用 |
|------|------|
| `write_outline` | 生成大纲 |
| `append_body` | 续写下一章 |
| `consistency_check` | 对照大纲 + `story_bible.json` 做一致性检查 |
| `review_chapter` | 章节审阅（结果写入 `chapter_reviews.json`） |
| `polish_chapter` | 按审阅意见润色本章（`edit_text_artifact`） |
| `chapter_summary` | 更新 `story_bible.json` 章节摘要与伏笔 |
| `arc_checkpoint` | 卷/里程碑检查点（可配合 `action=pause`） |

进度追踪：`progress.writing_state.phases_done`（按章记录已完成阶段）。

| 来源 | 说明 |
|------|------|
| **显式计划** | `mission.work_plan.items` 或 LLM 调用 `set_mission_work_plan` |
| **动态追加** | LLM 调用 `enqueue_mission_work_item` |

相关工具：`read_text_artifact`、`edit_text_artifact`、`get_manuscript_context`、`enqueue_mission_work_item`、`set_mission_work_plan`。

### 强制介入（由规划模型决定，不是用户填 JSON）

用户只需自然语言，例如：「大纲不行，按亮剑重写」「把第 2 章那段对话改克制一点」。

**规划 LLM** 在 `planning` 输出里填写 `mission_intervention`（含 `force: true` 与否）；运行时据此覆盖 step_policy。用户不必、也不应手写 `force` 字段。

高级客户端仍可显式传入 `intervention`（调试 / 自动化集成）；与模型输出合并时，以 **`force: true` 的显式块** 为准。

仅发 `message` 的 steer：`POST /tasks/{id}/steer` → 下一轮 `mission_act` 前会走 **planning + 工具** 解读该消息，再执行（见 §11.2–§11.5）。

#### Turn contract（单轮可执行契约）

为避免「规划写 edit_plot、执行却 append_body」的多通道漂移，planning 之后会物化 **`turn_contract`**（`app/services/turn_contract.py`）：

| 字段 | 作用 |
|------|------|
| `primary_op` | 本回合主操作：`edit_plot`、`append_body`、`write_outline`… |
| `tools` / `ops` | 本回合必须先执行的非写作工具 |
| `forbid` | 本回合禁止的写作动作（如 `append_body`） |
| `override_step_policy` | 为 true 时，`resolve_writing_intent_for_step` 不再退回 `step_policy.then` |

来源（按优先级）：

1. 规划 JSON 显式 `turn_contract`
2. `mission_intervention`（含 `coerce_steer_intervention` 结果）
3. **结构化推断**：`selected_tools` 含 read+edit 且 `writing_intent.enabled=false` → 自动补全 `edit_plot` + `force`（steer 规划轮）

调度规则：

- `route_after_planning` / `route_after_tool`：contract 禁止写作时 **不得** 进入 `writing_node`
- `run_subgraph_writing`：禁止写作时改走 `run_pipeline_request`（工具链），**不再**隐式 `append_body`
- `execute_mission_step`：`subgraph:writing` 在 contract 禁止时改走 pipeline
- `reflection`：`validate_turn_contract_execution` 对比 `turn_facts`，可触发 `retry_planning`

规划 prompt 可选输出 `turn_contract`；与 `mission_intervention` 等价。Steer 材料变更时建议 `forbid: ["append_body"]` 并配合 `work_plan_patch` 取消 pending 续写项。

**Planning 解析容错**（`app/services/llm_client.py` + `planning_fallback_from_state`）：

1. 流式仅返回 thinking、无 text → 自动 **非流式重试** 一次  
2. 截断 JSON → `_repair_truncated_planning_json`  
3. 仍失败 → **LLM JSON repair**（一次）  
4. 仍失败 → 按 mission / `work_plan` 队列 **结构性 fallback**（如 pending `edit_plot` → read/edit 工具链），避免整轮 `FAILED`  

审计指标：`planning_parse_fallback`、`planning_parse_llm_repaired`。

**大纲内设定更正**（如「林远舟是……」）：当 `outline_status.outline_complete` 时，规划应走 **`edit_plot`**（勿 `rewrite_outline`；误选时由 `coerce_steer_intervention` 降级）。执行顺序由模型 + 工具完成，而非整篇重写：

1. 工具 **`read_text_artifact`** 读取 `outline.txt`
2. **规划模型**根据读到的正文与用户纠正，判定 `old_text` / `new_text` 锚点（`plan_edit_from_read_content`）
3. 工具 **`edit_text_artifact`** 按锚点局部替换

规划若已在 `tool_params` 中给出精确 `old_text`/`new_text`，可跳过第 2 步的再规划。仅当用户明确要求整份重写大纲时才用 `rewrite_outline`。

`POST /tasks/{id}/resume` 签发 `execution_grant` 并执行下一工作项（或批准待确认闸门）。关闭编排：`"orchestration": {"enabled": false}`。

编排队列与手稿不一致时，由 `reconcile_work_plan` 在 `mission_observe` / `mission_decide` 自动对齐（谓词见 [`MISSION_EXECUTION_CONTROL.md`](MISSION_EXECUTION_CONTROL.md)）。

### 11.1 Steer 投递路径（Web / API）

| 场景 | 客户端行为 | 服务端 |
|------|------------|--------|
| **Mission 流式运行中**（`running=true`） | Web CLI 输入 → `POST /tasks/{id}/steer` | 消息进入 `pending_user_message` 队列；当前步（含正文流式）结束后在 `mission_eval` 消费并 `apply_steer_message` |
| **已暂停**（`MISSION_PAUSED` / `REASONED`） | `POST /resume` 或 steer | **`/resume`**：签发 `execution_grant`（`source=resume_api`），下一轮 `mission_decide` 可 `continue` 进入写作；仅 message 的 steer 仍可能挂 `require_planning_after_steer` |
| **刷新页面后同 session 再提交** | `POST /tasks/stream`，`session_id` 不变、`new_session=false` | **Session turn**：`turn_policy` 判定 `resume_mission` 时，机械续写信号签发 **execution_grant** 并跳过规划闸门，直接 `apply_mission_step_to_payload`；材料级 steer 仍走 planning；「查看/检阅 + 大纲」走 `review_outline` |

**注意**：刷新后输入 ≠ 运行中插队；若 session 仍在 `MISSION_RUNNING`，应先等暂停或确认服务端状态，避免与后台续写并行误解。

实现：`app/services/mission_execution.py`、`app/services/mission_steer.py`、`app/services/session_turn.py`、`app/services/graph_runner._prepare_mission_for_turn`。

#### Steer 优先级 / 抢占提示（best-effort）

运行中插队默认是「下一步边界应用」。为降低“介入来不及”的体感，`POST /tasks/{id}/steer` 支持额外字段：

```json
{
  "message": "请停下并重写大纲",
  "priority": 1,
  "preempt": true
}
```

- `priority>0`：表示高优先级介入（用于写作生成前的快速停止判断）
- `preempt=true`：抢占提示（best-effort）

当前实现点：
- 写作分段 append（`append_body`）已支持检测 `has_pending_steer()` 并提前结束分段循环
- 生成新草稿前（`generate_artifact_content`）会检查是否存在 **高优先级** queued steer，尽量避免启动新的长生成（best-effort）

注意：这不是强制中断正在进行的外部 LLM 请求，而是在“下一次生成/下一段生成”前尽快收敛。

### 11.2 双阶段人工确认（规划前 / 执行后）

材料级介入（如 `rewrite_outline`、`reset_body`、总字数变更等）采用 **两道闸门**，避免误解意图或产物：

| 阶段 | 时机 | 状态字段 | 用户动作 |
|------|------|----------|----------|
| **Intent（理解）** | planning 解读 steer 之后、`mission_act` 重操作之前 | `steer_intent_pending_confirm`、`steer_intent_confirmation` | 结构化批准（§11.3） |
| **Outcome（结果）** | 实质性工作项完成且无失败（如 `write_outline`、`edit_plot`、`append_*` 打断）之后 | `steer_outcome_pending_confirm`、`steer_outcome_confirmation` | 同上；预览见 `sections[]` / `artifact_excerpt`（策略见 [`CONFIRMATION_GATES.md`](CONFIRMATION_GATES.md)） |

未批准前：`progress_evaluator` 返回 `MISSION_PAUSED`；`mission_act` 不继续写正文。`autonomous` 在任一门闸 pending 时 **不** 自动 `resume`。

实现：`app/services/confirmation/`（gate / preview / block_builder）、`mission_steer_confirm.py`、`mission_steer_outcome_confirm.py`、`mission/steer_replan.py`、`mission_observe_node.py`。

### 11.3 结构化批准 API（不用自然语言口令）

**不**用「确认」「ok」等短语表匹配用户意图（与 §11 总原则一致）。批准仅接受显式字段：

```http
POST /tasks/{task_id}/resume
Content-Type: application/json

{"confirm": true}
```

或（任务已暂停时）：

```http
POST /tasks/{task_id}/steer
Content-Type: application/json

{"confirm": true}
```

Web CLI：`/confirm` 等价于 `resume` 且 `confirm:true`（CLI 别名，非 NLP）。

SSE / 确认块内附带机器可读动作（`confirmation.user_actions` / `confirmation_actions`）：

```json
{
  "resume": {"method": "POST", "path": "/tasks/{id}/resume", "body": {"confirm": true}},
  "steer":  {"method": "POST", "path": "/tasks/{id}/steer",  "body": {"confirm": true}},
  "cli_alias": "/confirm"
}
```

实现：`app/services/steer_confirmation_actions.py`、`app/api/task_api.py`（`ResumeTaskRequest`、`SteerTaskRequest`）。

### 11.6 客户端展示（`client_display`，避免 Web 硬编码）

服务端统一组 **`system_lines`** / **`autonomous_ui`** / **`client_display`**，Web 只渲染字符串与结构化行为，**不**根据 `steer_action === "rewrite_outline"` 等分支写中文模板。

| 字段 | 来源 | 用途 |
|------|------|------|
| `system_lines` | `mission_intervention.reason`（规划 LLM）、`steer_intent_summary`、`mission_control.reason`、编排摘要 | `mission_paused` / steer API 系统行 |
| `autonomous_ui.behavior` | 程序判定门闸状态 | `auto_resume_step` / `wait_intent_confirm` / `resume_after_steer` 等 |
| `client_display` | `POST /steer` 响应 | 运行中插队反馈 |
| `confirmation.display.title` | 服务端 phase 标签 | 确认面板标题 |

规划 JSON 中 `mission_intervention.reason` 为可选 **用户可见** 一句说明（由模型生成，非前端写死）。

实现：`app/services/client_display.py`。

### 11.4 状态持久化（steer 字段不被 act 冲掉）

LangGraph 节点快照常省略 steer 相关字段。`StateStore.save()` 在写入前合并：

- **顶层**：`pending_user_message`、`steer_applied_at`、intent/outcome 确认标志等  
- **`input_payload` 内**：`require_planning_after_steer`、`steer_planning_done`、`mission_intervention`、`steer_watch_outcome` 等  

否则 `mission_act` / `writing_node` 保存后续跑会丢失「必须先 planning」闸门，表现为刷新或 pause 后直接 `append_body` 写 novel。

实现：`app/services/state_store.py`（`_VOLATILE_STATE_KEYS`、`_PAYLOAD_VOLATILE_KEYS`）。

### 11.7 Streaming update 合并（避免 work_plan 状态回滚）

Mission graph 的 streaming 更新块可能只包含局部字段。为了避免浅合并导致 `progress.work_plan` 等嵌套结构被覆盖/回滚，运行时合并策略为：

- `merge_state()` 对 `progress`、`input_payload` 等嵌套 dict 进行合并（保留既有键）
- `stream_mission_graph()` 用 `merge_state(latest, **update)` 合并 chunk，而不是 `{**latest, **update}`

目的：修复 UI/控制循环出现「工作项已 done 但又显示 pending」等错位。

### 11.5 Session turn 与规划闸门

同 session 新 goal（`prepare_session_turn` + `_prepare_mission_for_turn`）时：

1. `_append_steer_goal` 合并用户纠正  
2. `apply_steer_planning_gate` → `require_planning_after_steer=true`  
3. `writing_intent.enabled=false`（`await_steer_planning`），避免沿用上一轮 `append_body`  
4. `prepare_state_for_mission_act`：只要 `steer_requires_planning`，**强制**关闭写作并走 `run_pipeline_request`（含 `planning_node`）

审计中应出现 **`planning`** 节点；若只有 `mission_act` → `subgraph:writing`，检查容器是否已部署上述逻辑。

### autonomous 与 Web 介入

| 模式 | 行为 |
|------|------|
| `mission.autonomous: true` | 编排开启时默认 `stepwise: false`，单轮 SSE 内连续多步直至达标、`max_steps` 或墙钟上限 |
| `interactive` + `stepwise: true` | 每完成一个 work item → `MISSION_PAUSED`，需 `POST /tasks/{id}/resume` |
| Web CLI 运行中输入 | `POST /tasks/{id}/steer` 排队/应用强制介入，不阻塞为“必须点继续” |
| Intent / Outcome 门闸 pending | 不自动 `resume`；Web 展示确认块，见 §11.2–§11.3 |
| 刷新页面后提交 | Session turn；须清掉旧 `writing_intent`，见 §11.5 |
| 输出状态 | 编排未完成时保持 `MISSION_PAUSED`，避免误标 `COMPLETED` 导致无法 resume |

### 步数预算（`budget.max_steps`）

- 规划模型可在 `mission.budget.max_steps` 中自选（硬顶 **500**，见 `mission.steps_hard_cap`）
- 未指定时按 `total_target_chars / chars_per_step`（+ 大纲一步）机械估算
- 详见 `app/services/mission_schema.py` 中 `resolve_mission_budget_dict`

### 与「多 Agent / 子 Agent」的关系

- **Mission 写作**：单控制循环 + **模型自选 `writing_phase`**（写 / 审 / 润 / 摘要 / 一致性 / 卷检查点），**不是**每章自动 spawn 独立 Agent 进程
- **Supervisor**：`task_type=supervisor` 时多 worker 分工，面向通用多域子任务，**默认不与**长篇 mission 写作链打通
- 若需真·并行多 Agent，需在 Supervisor 域定制 decompose + 共享 manuscript 目录与合并策略

**延伸阅读**：[`ROUTE_AUDIT.md`](ROUTE_AUDIT.md) · [`REASONING_SHORTCUT.md`](REASONING_SHORTCUT.md)。
