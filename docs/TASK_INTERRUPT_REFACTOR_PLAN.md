# 长任务中断与恢复重构方案

> **状态：已完整实施** — Phase 1–4 全部完成；OMAW worker-scope 与 writing phase 全量迁移已落地  
> **范围**：Mission Runtime / Manuscript Writing / Graph Runner / Worker Runtime  
> **依据**：[`debug.log`](../debug.log) · [`MISSION_EXECUTION_CONTROL.md`](./MISSION_EXECUTION_CONTROL.md) · [`MANUSCRIPT_WRITING.md`](./MANUSCRIPT_WRITING.md)

## 实施状态总览（2026-06-05）

| 阶段 | 状态 | 说明 |
|------|------|------|
| Phase 1 控制面 | ✅ 完成 | `task_control` / `execution_control` / API / 前端 Stop 双语义 |
| Phase 2 执行器合作 | ✅ 完成 | writing / reasoning / tool / mission 图 + OMAW worker 已接入 |
| Phase 3 提交与恢复 | ✅ 完成 | `StepCommitter` + checkpoint resume；`write_body` / writing phase 全量迁移 |
| Phase 4 写作 chunk 化 | ✅ 完成 | `append_body` 段落级 + `write_outline` 块级流式 commit |
| §13 OMAW Worker | ✅ 完成 | worker-scope 控制 + `mission_oma/workers.py` 接入 |
| §17 审计与指标 | ✅ 完成 | 审计事件 + `metrics_service` 计数/延迟 |
| §18 测试 | ✅ 完成 | 单元 / 节点 / 集成测试均已添加并通过 |

### 已落地文件

| 模块 | 路径 |
|------|------|
| TaskControl 注册表 | [`app/services/task_control.py`](../app/services/task_control.py) |
| 执行器检查协议 | [`app/services/execution_control.py`](../app/services/execution_control.py) |
| Step 提交边界 | [`app/services/step_committer.py`](../app/services/step_committer.py) |
| Live 快照扩展 | [`app/services/live_task_state.py`](../app/services/live_task_state.py) |
| Graph 中枢 | [`app/services/graph_runner.py`](../app/services/graph_runner.py) |
| HTTP API | [`app/api/task_api.py`](../app/api/task_api.py) |
| 手稿原子同步 | [`app/services/manuscript_service.py`](../app/services/manuscript_service.py) (`sync_manuscript_snapshot_atomic`) |
| 长任务 resume 提示 | [`app/services/long_running_task.py`](../app/services/long_running_task.py) |
| OMAW Worker 控制 | [`app/services/mission_oma/workers.py`](../app/services/mission_oma/workers.py) |
| Writing phase checkpoint | [`app/services/writing_phases.py`](../app/services/writing_phases.py) |
| 流式块级 commit | [`app/services/llm_gateway.py`](../app/services/llm_gateway.py) (`StreamingStepSession`) |
| 前端 Stop | [`web/static/app.js`](../web/static/app.js) (`interrupt-stream` + `pause`) |

### API（已实现）

- `POST /tasks/{id}/interrupt-stream` — 仅停 SSE
- `POST /tasks/{id}/pause` — 安全点暂停（Stop 默认语义）；body 可选 `worker_id` 指定 worker scope
- `POST /tasks/{id}/cancel` — 取消，保留已提交产物；body 可选 `worker_id`
- `POST /tasks/{id}/workers/{worker_id}/pause` — worker-scope 暂停
- `POST /tasks/{id}/workers/{worker_id}/cancel` — worker-scope 取消
- `GET /tasks/{id}/control` — 控制态可观测（含 `worker_controls`）
- `POST /tasks/{id}/stop` — `pause` 别名（兼容旧客户端）

### 运行测试

```bash
python3 -m pytest \
  tests/services/test_task_control.py \
  tests/services/test_execution_control.py \
  tests/services/test_step_committer.py \
  tests/integration/test_task_pause_resume_checkpoint.py \
  tests/integration/test_cancel_preserves_committed_artifacts.py \
  tests/integration/test_disconnect_does_not_cancel_task.py \
  tests/nodes/test_writing_pause_at_boundary.py \
  tests/nodes/test_writing_cancel_mid_stream.py \
  tests/nodes/test_reasoning_cancel_does_not_deadletter.py \
  tests/api/test_task_api.py::test_task_control_endpoints \
  -q
```

---
## 1. 背景

当前系统已经具备较成熟的长任务能力，包括：

- Mission 控制面与 [`execution_grant`](./MISSION_EXECUTION_CONTROL.md)
- 手稿真相源与 [`writing_intent`](./MANUSCRIPT_WRITING.md)
- `work_plan` / agenda / DAG 扩展
- Writing 节点、streaming write、steer 双阶段确认
- [`graph_runner.py`](../app/services/graph_runner.py) 作为 HTTP/API 到 runtime 的唯一执行桥梁

但从 [`debug.log`](../debug.log) 的运行轨迹来看，用户在写作中“打断任务”的体验仍然不够灵活，核心问题不是 UI 少了一个 stop 按钮，而是后端尚未形成统一、可恢复、可提交边界明确的 interrupt architecture。

## 2. 问题诊断

### 2.1 从日志观察到的症状

在 [`debug.log`](../debug.log) 中可以看到：

- [`debug.log:132`](../debug.log:132) 开始写作：`开始写作 action=write_outline`
- [`debug.log:150`](../debug.log:150) 很快显示 writing 完成
- 真正长时间运行的是 [`debug.log:154`](../debug.log:154) 到 [`debug.log:210`](../debug.log:210) 的持续流式生成
- [`debug.log:220`](../debug.log:220) 表示已经写入 [`outline.txt`](../outline.txt)
- 但 [`debug.log:152`](../debug.log:152) 的 mission snapshot 中仍然是 `outline_bytes=0`、`written_chars=0`

同时还可以观察到：

- [`debug.log:52`](../debug.log:52) 到 [`debug.log:60`](../debug.log:60) 先路由到 `qa_mode`
- [`debug.log:102`](../debug.log:102) 到 [`debug.log:110`](../debug.log:110) 又切回 `manuscript_mode`
- [`debug.log:68`](../debug.log:68) 与 [`debug.log:92`](../debug.log:92) 出现 `contract forbids active writing action write_outline`，但执行仍继续

### 2.2 根因分析

当前的“中断不灵活”本质上来自以下几个结构性问题：

1. **长生成仍是单体动作**  
   写作虽然名义上经过 writing 节点，但实际上用户体感仍是“一次长回合生成”，没有稳定的 step commit 边界。

2. **控制面与执行面分离不彻底**  
   当前存在 pause / grant / steer / pending_user_message 等控制信号，但缺少统一的任务级 interrupt protocol。

3. **产物提交与状态推进不同步**  
   日志显示文件已写，而 snapshot 仍显示 0 bytes，说明 state checkpoint 与 artifact commit 存在顺序或一致性问题。

4. **前端 stop 与后端 cancel 语义混杂**  
   当前更像是“停流”或“下一边界 best-effort 收敛”，而不是明确的任务暂停、任务取消、从 checkpoint 恢复。

5. **写作与推理边界不够清晰**  
   writing 节点很快结束，但长耗时发生在 reasoning streaming，说明“产物生成”“结果展示”“状态结算”并没有完全对齐。

## 3. 目标

本方案采用一个统一方向：

**将长任务系统重构为可暂停、可取消、可恢复、可检查点提交的统一任务运行时。**

目标如下：

1. 用户停止后，界面立即停流。
2. 后端能感知暂停/取消，而非继续无意义生成。
3. 当前 step 若未提交，则安全放弃；已提交 step 可恢复。
4. 恢复从最近 checkpoint 开始，而不是从头重写。
5. 不靠 prompt 硬编码，也不靠自然语言口令推断控制语义。
6. 能兼容 manuscript mission，也能扩展到 reviewer/editor/planner 等 worker。

## 4. 方案总览

方案名称：**Unified Interruptible Mission Runtime**

核心策略：

- 在 [`graph_runner.py`](../app/services/graph_runner.py) 之上建立统一控制面
- 为每个运行中的任务引入独立的 TaskControl 对象
- 把“停止”拆成三种明确语义：`interrupt_stream`、`pause_task`、`cancel_task`
- 所有长步骤都必须通过 step buffer + step commit 执行副作用
- 所有恢复都基于 checkpoint，而不是依靠 transcript 猜当前做到哪里

## 5. 控制语义设计

### 5.1 `interrupt_stream`

仅停止前端流式输出，不改变任务运行状态。

适用场景：
- 用户不想继续看屏幕刷字
- 任务允许后台继续

说明：
- `interrupt_stream` 不能等价于任务取消
- client disconnect 也不能自动解释为 cancel

### 5.2 `pause_task`

请求任务在最近安全点暂停。

规则：
- 如果当前 step 已达到 commit 边界，则提交后暂停
- 如果当前 step 尚未提交，则按 step 类型决定是否允许 partial commit
- 暂停后任务进入 `MISSION_PAUSED`
- 后续通过 [`/resume`](../app/api/task_api.py) 或等效机制从 checkpoint 恢复

这是写作任务默认的“停止”语义。

### 5.3 `cancel_task`

终止任务并标记取消。

规则：
- 未提交的 step buffer 丢弃
- 已提交内容保留
- 任务进入 `CANCELLED`
- 默认不允许 resume，仅允许新建任务或显式复制上下文重跑

## 6. 状态机设计

建议在现有 [`TaskStatus`](../app/runtime/state.py) 之上增加一个控制子状态层。

### 6.1 顶层任务状态

- `MISSION_RUNNING`
- `MISSION_PAUSED`
- `COMPLETED`
- `FAILED`
- `WAITING_REVIEW`
- `CANCELLED`

### 6.2 控制子状态

写入 `state["interrupt_context"]["control_state"]`（持久化层；live 快照见 `LiveTaskEntry.control_state`）：

- `IDLE`
- `RUNNING_STEP`
- `STREAMING_OUTPUT`
- `COMMITTING_STEP`
- `PAUSE_REQUESTED`
- `PAUSED_AT_CHECKPOINT`
- `CANCEL_REQUESTED`
- `CANCELLED_DURING_STEP`
- `FAILED_DURING_STEP`

### 6.3 Step 生命周期

每个 step 应具有独立元数据：

```json
{
  "step_id": "ms_12_append_body_v4",
  "attempt": 1,
  "status": "running|committing|committed|aborted|cancelled",
  "kind": "append_body",
  "artifact_targets": ["novel.txt"],
  "buffered_chars": 0,
  "committed_chars": 0,
  "started_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

## 7. 数据模型重构

## 7.1 新增 `TaskControl`

建议新建 [`app/services/task_control.py`](../app/services/task_control.py)，定义：

```python
@dataclass
class TaskControl:
    task_id: str
    run_id: str
    stream_interrupted: bool = False
    pause_requested: bool = False
    cancel_requested: bool = False
    requested_at: str | None = None
    requested_by: str | None = None
    reason: str | None = None
```

提供统一接口：

- `register_task_control()`
- `request_interrupt_stream()`
- `request_pause()`
- `request_cancel()`
- `snapshot_task_control()`
- `clear_task_control()`

### 7.2 重构 [`live_task_state.py`](../app/services/live_task_state.py)

当前 [`LiveTaskEntry`](../app/services/live_task_state.py:13) 仅保存：

- `state`
- `updated_at`
- `running`

长期不够。建议扩展 live snapshot，但**不要**把用户控制命令直接写进 live state：

```python
@dataclass
class LiveTaskEntry:
    state: AgentState
    updated_at: str
    running: bool = True
    run_id: str | None = None
    control_state: str = "IDLE"
    active_step_id: str | None = None
    active_generation_id: str | None = None
```

职责划分：

- [`live_task_state.py`](../app/services/live_task_state.py)：最新运行态快照
- [`task_control.py`](../app/services/task_control.py)：用户控制意图 registry

### 7.3 在持久化 state 中增加 `interrupt_context`

建议增加：

```python
state["interrupt_context"] = {
  "pause_requested": False,
  "cancel_requested": False,
  "last_control_event": null,
  "active_step": {...},
  "last_committed_step": {...},
  "resume_from_checkpoint": {...}
}
```

该字段用于跨进程、刷新页面、worker 漂移后的恢复，不能只依赖进程内内存。

## 8. API 设计

建议新增以下控制接口：

- `POST /tasks/{id}/interrupt-stream`
- `POST /tasks/{id}/pause`
- `POST /tasks/{id}/cancel`
- `GET /tasks/{id}/control`

不建议继续把所有语义堆在 [`/resume`](../app/api/task_api.py) 或 [`/steer`](../app/api/task_api.py) 上。

### 8.1 请求体

```json
{
  "reason": "user_requested",
  "requested_by": "web"
}
```

### 8.2 响应体

```json
{
  "task_id": "...",
  "accepted": true,
  "control_action": "pause_task",
  "effective_state": "PAUSE_REQUESTED",
  "active_step_id": "ms_12_append_body_v4"
}
```

## 9. 执行器协议

建议新建 [`app/services/execution_control.py`](../app/services/execution_control.py)，作为所有长执行路径共享的检查协议。

提供：

- `check_for_control_signal()`
- `raise_if_cancel_requested()`
- `should_pause_at_boundary()`
- `mark_step_boundary()`

### 9.1 必查位置

所有长任务执行器必须在以下位置检查控制信号：

1. 节点开始前
2. 模型请求发起前
3. 每个 stream chunk 到达时
4. 每个 writing delta 解析后
5. artifact commit 前
6. step commit 后
7. 节点退出前

### 9.2 控制异常

定义异常：

- `PauseRequested`
- `CancelRequested`
- `StreamInterrupted`

约束：

- `PauseRequested` 不是失败
- `CancelRequested` 不是 dead letter
- `StreamInterrupted` 不是任务生命周期结束

## 10. 对 [`graph_runner.py`](../app/services/graph_runner.py) 的重构

[`graph_runner.py`](../app/services/graph_runner.py) 是本次改造的主入口。

### 10.1 graph run 生命周期注册 control

在 [`_begin_task_graph_run()`](../app/services/graph_runner.py:181) 中，除了现有 [`begin_graph_run()`](../app/services/graph_runner.py:184)，还应注册 `TaskControl`。

### 10.2 流式路径传播控制信号

无论是 `stream_graph` 还是 `stream_mission_graph`，GraphRunner 都应：

- 定期读取 control snapshot
- 当 `stream_interrupted=true` 时停止 SSE 输出
- 当 `pause_requested=true` 或 `cancel_requested=true` 时，把控制信号传播给当前运行节点

### 10.3 `_finalize_turn()` 统一结算

当前 [`_finalize_turn()`](../app/services/graph_runner.py:491) 主要做 trace/history 结尾。改造后应增加：

- pause/cancel 的最终状态结算
- `interrupt_context` 持久化
- control registry 清理
- 审计事件写入

### 10.4 区分 disconnect 与 cancel

当前 [`_format_stream_exception()`](../app/services/graph_runner.py:194) 中的 `GeneratorExit` 语义过于模糊，不应把“客户端断开”和“任务被取消”混为一谈。

## 11. 对 Manuscript Writing 的改造

### 11.1 写作必须 step 化

写作任务不再被视为一个长文本动作，而是明确 step：

- `write_outline`
- `append_body`
- `review_chapter`
- `polish_chapter`
- `chapter_summary`

每个 step 都必须有：

- step id
- generation id
- step buffer
- commit boundary
- committed checkpoint

### 11.2 引入 `StepBuffer` 与 `StepCommitter`

建议：

- 在 [`app/services/step_committer.py`](../app/services/step_committer.py) 中引入 `StepBuffer`（已实现）
- [`app/services/writing_stream.py`](../app/services/writing_stream.py) 负责 delta 级控制检查（已实现 `task_id` 参数）

职责分别为：

**StepBuffer**
- 聚合流式输出
- 跟踪 buffered chars / sections / paragraph boundaries
- 维护 generation metadata

**StepCommitter**
- 校验内容
- 提交 artifact
- 刷新 manuscript snapshot
- 写 checkpoint
- 返回 committed delta

### 11.3 partial commit 策略

不建议无差别允许 partial commit。

推荐：

- `write_outline`：默认不做 partial commit
- `append_body`：允许按段落或 section 边界 partial commit
- `review_chapter`：要求结构完整
- `polish_chapter`：原子提交
- `chapter_summary`：结构完整提交

### 11.4 checkpoint-first 提交顺序

必须固定提交顺序：

1. 校验 buffer
2. 写 staged artifact / temp 结果
3. 原子更新 manuscript metadata
4. 写 state checkpoint
5. 发 committed SSE / progress event

不能先对外宣称“已完成”，再补状态。

## 12. 对 Mission Control 的改造

> **状态**：§12.1–12.3 ✅ 全部完成（含 `attempt` / `last_run_id`）

### 12.1 `pause_reason` 需要区分来源 ✅

现有 [`pause_reason`](./MISSION_EXECUTION_CONTROL.md) 已有：

- `step_checkpoint`
- `gate_intent`
- `gate_outcome`
- `worker_lost`
- `budget`
- `forced`
- `failure`

建议新增（已在 [`mission_execution.py`](../app/services/mission_execution.py) 落地）：

- `user_requested_pause` — `PAUSE_USER_REQUESTED_PAUSE`
- `user_requested_cancel` — `PAUSE_USER_REQUESTED_CANCEL`

用于区分：
- 系统出于流程控制暂停
- 用户主动要求暂停或取消

### 12.2 `execution_grant` 继续保留，但职责收缩 ✅

[`execution_grant`](./MISSION_EXECUTION_CONTROL.md) 仍然保留，只负责：

- 从 pause 中恢复执行
- 机械 continue 的许可

不再承担任务运行期的 interrupt 控制职责。运行期控制一律进入 `TaskControl`。

### 12.3 `work_plan` item 增加执行元数据

建议每个工作项增加：

```json
{
  "id": "wp_12",
  "kind": "append_body",
  "status": "pending|running|paused|done|blocked|cancelled",
  "attempt": 2,
  "last_run_id": "...",
  "last_step_id": "...",
  "committed": true,
  "checkpoint_ref": "..."
}
```

这样 resume 不再需要猜测该 item 做到哪一步。

## 13. 对 OMAW / Worker Runtime 的兼容

> **状态**：§13.1–13.2 ✅ 全部完成

当前系统已经朝 OMAW 演进，因此中断协议必须是跨 worker 的，而不只是 manuscript 特化。

### 13.1 控制作用域

未来建议支持：

- task-scope control：暂停/取消整个 mission
- worker-scope control：暂停某个 reviewer/editor worker

**当前**：task-scope 与 worker-scope pause/cancel/interrupt-stream 均已可用；`interrupt_context.worker_controls` 持久化 worker 控制意图。

### 13.2 worker 协作取消 ✅

所有 worker 执行前后共享同一套 [`execution_control.py`](../app/services/execution_control.py) 协议：

- [`mission_oma/workers.py`](../app/services/mission_oma/workers.py) 入口与 LLM 调用前检查控制信号
- `PauseRequested` / `CancelRequested` 经 `handle_control_exception` 结算，不进入 `handle_worker_failure`
- [`writing_phases.py`](../app/services/writing_phases.py) 各 phase 完成时经 `commit_phase_checkpoint` 写入 checkpoint

## 14. 与 Cursor / Copilot 的工程对照

类似 [`Cursor`](https://cursor.com) 与 [`Copilot`](https://github.com/features/copilot) 的系统之所以打断体验更好，核心不在 stop 按钮，而在：

1. action horizon 更短
2. patch/edits 是结构化产物，不是整篇长流式输出
3. 工具执行与模型生成边界清晰
4. 已提交结果与执行状态强绑定
5. stop 是任务状态机动作，而不是 UI 停止接收 token

你的系统不是要照搬 IDE copilot 的交互，而是要吸收它们背后的工程原则：

- 小步执行
- 明确提交
- 统一控制
- 可恢复运行

## 15. 分阶段实施计划

### Phase 1：建立控制面 ✅

目标：先有统一 pause/cancel/interrupt-stream 能力。

改造（均已落地）：

- 新建 [`app/services/task_control.py`](../app/services/task_control.py)
- 新建 [`app/services/execution_control.py`](../app/services/execution_control.py)
- 改造 [`live_task_state.py`](../app/services/live_task_state.py)
- 改造 [`graph_runner.py`](../app/services/graph_runner.py)
- 在 [`task_api.py`](../app/api/task_api.py) 增加 pause/cancel/interrupt-stream API

验收（已通过）：
- 用户可以明确请求 pause/cancel
- disconnect 不会被误当 cancel
- 控制状态可观测

### Phase 2：执行器合作取消 ✅

目标：让 writing / reasoning / tool execution 真正响应控制信号。

改造（已落地）：

- 流式路径周期性检查 `TaskControl` — `writing_stream` / `llm_gateway` / `graph_runner` SSE 泵
- 引入 `PauseRequested` / `CancelRequested` — `execution_control.py`
- GraphRunner 统一捕获、落状态、写审计 — `_stream_error` / `_finalize_turn`
- 接入节点：`writing_node` / `reasoning_node` / `tool_node` / `mission_decide_node` / `mission_graph`
- OMAW worker：`mission_oma/workers.py` + worker-scope 控制检查

验收（已通过）：
- 长生成中的 pause/cancel 能在短延迟内生效
- 不误触发 dead letter 或 retry

### Phase 3：提交与恢复统一 ✅

目标：建立 commit-first / checkpoint-first 语义。

改造（已落地）：

- 新建 [`step_committer.py`](../app/services/step_committer.py)
- `write_outline` / `write_body` / `append_body` 经 `StepCommitter` 落盘（`writing_node.py`）
- `writing_phases` 各 action 完成时经 `commit_phase_checkpoint` 写入 checkpoint
- `work_plan` item 持有 `checkpoint_ref` / `committed` / `last_step_id` / `attempt` / `last_run_id`
- `resume` 基于最近 committed step（`apply_checkpoint_to_resume_state` in `prepare_resume_mission`）

验收（已通过）：
- 不再出现“文件已写但 state 仍为 0 bytes”
- pause 后 resume 从 checkpoint 继续

### Phase 4：写作 chunk 化 ✅

目标：让打断真正细粒度。

改造：

- `append_body` 改为 paragraph/section chunk — ✅ `StepCommitter.commit_at_paragraph_boundary` + 分 chunk append 循环
- `write_outline` 支持 block 级生成 — ✅ `StreamingStepSession` + `commit_at_outline_block_boundary`（`llm_gateway` 流式路径）
- steer 与 pause 合流为统一控制语义 — ✅ pause/cancel 走 `TaskControl`，不再 queue steer pause

验收（已通过）：
- 停止时通常只损失当前小段 — ✅ append_body 段落级
- 恢复不会重复写已提交内容 — ✅ checkpoint + work_plan 去重

## 16. 重点改造文件

### 第一优先级 ✅

- [`app/services/graph_runner.py`](../app/services/graph_runner.py)
- [`app/services/live_task_state.py`](../app/services/live_task_state.py)
- [`app/services/long_running_task.py`](../app/services/long_running_task.py)
- [`app/api/task_api.py`](../app/api/task_api.py)

### 第二优先级 ✅（主路径）

- [`app/nodes/writing_node.py`](../app/nodes/writing_node.py) — `write_outline` / `write_body` / `append_body` 已接 StepCommitter
- [`app/nodes/reasoning_node.py`](../app/nodes/reasoning_node.py)
- [`app/services/writing_stream.py`](../app/services/writing_stream.py)
- [`app/services/manuscript_service.py`](../app/services/manuscript_service.py)
- [`app/runtime/mission_graph.py`](../app/runtime/mission_graph.py)

### 第三优先级 ✅ / 无需改动

- [`app/services/graph_run_registry.py`](../app/services/graph_run_registry.py) — 沿用现有 `begin_graph_run` / `end_graph_run`
- [`app/services/audit_store.py`](../app/services/audit_store.py) — 经 `append_audit` + `record_control_event` 写入
- [`app/services/client_display.py`](../app/services/client_display.py) — `user_requested_pause/cancel` 文案

## 17. 审计与可观测性 ✅

已实现审计事件（经 `task_control` / `step_committer` audit_log）：

| 事件 | 实现位置 |
|------|----------|
| `task_pause_requested` | `graph_runner._apply_control_request` → `record_control_event` |
| `task_pause_observed` | `execution_control.handle_control_exception` |
| `task_paused_at_checkpoint` | `execution_control.finalize_control_outcome` |
| `task_cancel_requested` | 同上 pause_requested 路径 |
| `task_cancel_observed` | `handle_control_exception` |
| `task_cancelled` | `finalize_control_outcome` |
| `step_commit_started` | `step_committer.commit` |
| `step_commit_succeeded` | `step_committer.commit` |
| `step_commit_aborted` | `step_committer.abort_uncommitted` |
| `stream_interrupted` | `execution_control.handle_control_exception` / metrics |

已实现指标（[`app/services/metrics_service.py`](../app/services/metrics_service.py)）：

- `pause_latency_ms` — `observe_pause_latency_ms`
- `cancel_latency_ms` — `observe_cancel_latency_ms`
- `checkpoint_commit_ms` — `observe_checkpoint_commit_ms`
- `partial_commit_count` — `inc_partial_commit`
- `resume_from_checkpoint_count` — `inc_resume_from_checkpoint`
- `disconnect_without_cancel_count` — `inc_disconnect_without_cancel`（`GeneratorExit` 路径）

## 18. 测试策略 ✅

已新增并通过（2026-06-05）：

### 18.1 单元测试

- [`tests/services/test_task_control.py`](../tests/services/test_task_control.py)
- [`tests/services/test_execution_control.py`](../tests/services/test_execution_control.py)
- [`tests/services/test_step_committer.py`](../tests/services/test_step_committer.py)

### 18.2 节点测试

- [`tests/nodes/test_writing_pause_at_boundary.py`](../tests/nodes/test_writing_pause_at_boundary.py)
- [`tests/nodes/test_writing_cancel_mid_stream.py`](../tests/nodes/test_writing_cancel_mid_stream.py)
- [`tests/nodes/test_reasoning_cancel_does_not_deadletter.py`](../tests/nodes/test_reasoning_cancel_does_not_deadletter.py)

### 18.3 集成测试

- [`tests/integration/test_task_pause_resume_checkpoint.py`](../tests/integration/test_task_pause_resume_checkpoint.py)
- [`tests/integration/test_cancel_preserves_committed_artifacts.py`](../tests/integration/test_cancel_preserves_committed_artifacts.py)
- [`tests/integration/test_disconnect_does_not_cancel_task.py`](../tests/integration/test_disconnect_does_not_cancel_task.py)

## 19. 风险与规避

### 19.1 流式 state merge 覆盖控制信号

规避：
- 用户控制意图不写进易被 stream chunk 覆盖的位置
- 独立 `task_control` registry 持有控制命令

### 19.2 partial commit 造成半成品污染

规避：
- 仅允许特定 step 类型 partial commit
- partial 必须以段落/section/结构边界提交，不按裸字符阈值提交

### 19.3 resume 重复写入

规避：
- `last_committed_step_id` + `checkpoint_ref` 去重
- artifact commit 设计成幂等

### 19.4 状态机复杂度上升

规避：
- 顶层状态尽量不改，只扩展控制子状态
- 先在 manuscript mission 落地，再横向推广到 worker runtime

## 20. 最终决策建议

**该路线已于 2026-06-05 在 manuscript mission 主路径落地**（见文首「实施状态总览」）。长期仍建议：

**以 [`graph_runner.py`](../app/services/graph_runner.py) 为中枢，新增独立 [`task_control.py`](../app/services/task_control.py) 控制面、统一 [`execution_control.py`](../app/services/execution_control.py) 检查协议、标准化 `StepCommitter` 提交边界，把当前长写作执行模型重构为 checkpoint-first 的可暂停/可取消/可恢复任务状态机。**

这是长期最稳妥的方案，因为它：

- 不依赖硬编码文本判断
- 不把 stop 做成纯 UI 假动作
- 不把 manuscript 变成难以复用的专用特例
- 能与现有 mission / work_plan / agenda / OMAW 架构兼容

## 21. 最小可行实施顺序

实际落地顺序与验收状态：

1. ✅ 建立 [`task_control.py`](../app/services/task_control.py) 与 pause/cancel API
2. ✅ 改造 [`graph_runner.py`](../app/services/graph_runner.py) 传播控制信号
3. ✅ 改造 [`writing_node.py`](../app/nodes/writing_node.py) 与 [`writing_stream.py`](../app/services/writing_stream.py)，让写作流按 chunk 检查控制态
4. ✅ 新建 [`step_committer.py`](../app/services/step_committer.py)，统一 artifact commit 与 checkpoint
5. ✅ 改造 [`manuscript_service.py`](../app/services/manuscript_service.py)，保证提交与 manuscript snapshot 原子一致
6. ✅ reasoning、tool execution、OMAW worker runtime 均已接入

---

这份文档描述统一工程路线；截至 2026-06-05，**Mission / Manuscript / OMAW worker 全路径均已支持 pause / cancel / interrupt-stream / checkpoint resume**。