# 统一内核重构 · 进度追踪 (Unified Agent Core Rewrite)

> 配套方案见 `.cursor/plans/unified_agent_core_rewrite_*.plan.md`。
> 本文档持续更新，记录“已完成 / 进行中 / 阻塞 / 待办”的真实状态与验证证据。

最后更新：2026-06-09（stage 1 已提交）

**提交记录**
- `51ff046 [fix] old problem try fix` —— 预存 revision-intent WIP（重构开始前的未提交改动）。
- `5631877 [ReFactor] stage 1` —— 本统一内核第一阶段（Action 模型 + 删 17 备选图/节点 + 8 过时测试 + 运行时切换 + 去缠绕 + smoke 脚本 + 本文档）。

**当前核心行数基线（stage 1 后）**：`services 61.5k / nodes 4.36k / runtime 1.91k / domain 3.87k`（合计 ~71.7k）。
服务层批删是后续行数下降的主战场（目标核心合计 ~40–45k）。

---

## 0. 目标一句话

把 6 套并存执行范式收敛成**一套统一 agent 循环 + 一个小而明确的通用动作集**
（`answer / retrieve / read_artifact / write_artifact / edit_artifact / run_tool / run_code`），
批量删除写作/mission/exploration/ReAct 控制面，保留健康的叶子能力（artifact 工具、LLM、存储、检索、工程、supervisor、API），
让“意图 → 动作 → 参数 → 收敛”成为一条无翻译损耗的直线。

---

## 1. 环境约束（影响验证方式）

- **无 LLM Key、无外网**（huggingface / chroma 均被代理拦截）。
- 结果：现有集成测试在本环境**基线即红**（任何端到端路径都会以 `REJECTED` 收尾，与代码正确性无关）。
- **因此安全网改为**：
  1. `app/` 全量 import 扫描（0 失败）
  2. `build_agent_graph()` 编译通过
  3. `pytest --collect-only`（0 收集错误）
  4. LLM-free 单元测试（Action、artifact、router 逻辑）

验证命令：

```bash
HF_HUB_OFFLINE=1 python scripts/smoke_imports.py
HF_HUB_OFFLINE=1 python -m pytest tests/domain/test_action.py -q
HF_HUB_OFFLINE=1 python -m pytest tests --collect-only -q
```

---

## 2. 进度看板

图例：✅ 完成（已验证） · 🟡 部分 · ⛔ 阻塞 · ⬜ 待办

| # | 任务 | 状态 | 说明 |
|---|---|---|---|
| P0 | 安全网 | ✅ | 基线红已确认；改用 import 扫描 + 编译 + collect + LLM-free 单测 |
| P1 | Action 模型 | ✅ | `app/domain/action.py`；8 golden 测试全绿 |
| P1 | 统一 think 节点 | 🟡 | 写作分支已从主干剔除；**单一收敛门**未做（需重写 `progress_evaluator`/`verification` 活路径） |
| P1 | 单图主干 | 🟡 | 备选图已删；主干收敛为 reasoning；**路由器未合并为一**、`classify` 双轨未修 |
| P1 | QA 快路 | ⬜ | `planning` 已有 `qa_thin_skip`；132s 为 LLM 调用延迟，无 LLM 不可验证 |
| P2 | 运行时切换 | ✅ | `graph_runner` 仅走统一图 + supervisor；`session_controller` 不再调 mission 流方法 |
| P3 | 删图/节点/路由器 | ✅ | 删除 17 个备选图/节点/孤立路由器文件 |
| P3 | 删叶子服务 | 🟡 | 仅删了图/节点层；`writing/*`、mission/react 服务层未删 |
| P3 | 删 mission/写作服务 | ⛔ | 被 `planning_node`/`turn_contract` 等**活路径顶层引用**，需先重写大文件（需 LLM/测试环境验证） |
| P3 | 去缠绕 | 🟡 | `graph_runner`/`session_controller`/`graph_cache`/`mission_executor` 已去缠绕；`planning_node`/`turn_contract` 未动 |
| P3 | 清状态字段 | ⬜ | `state.py` 字段被活路径读取，需同步重写 |
| P4 | 叶子对齐 | 🟡 | `Action.is_edit_applied` 已给出编辑诚实契约；未接入 `tool_node` 执行路径 |
| P5 | 测试/配置清理 | 🟡 | 删 8 个过时测试、修复 `conftest`、1384 测试可收集；`config.yaml` 未清理 |
| P5 | 验收 | 🟡 | import 0 失败 + 编译 + 1384 collect + Action 8/8 + runtime 124/127（3 为预存失败）；全 e2e 无 LLM 不可验证 |

---

## 3. 本轮已落地变更

### 新增
- `app/domain/action.py` — 统一 `Action` 模型：与后端工具 1:1 映射；`is_edit_applied` 保证 0 替换 = **未完成**（修“改了没改”的静默成功）。
- `tests/domain/test_action.py` — 8 个 golden 测试（零翻译鸿沟 + 编辑诚实），全绿。
- `scripts/smoke_imports.py` — 快速 import + 图编译自检。

### 删除（17 源文件 + 8 测试文件）
- 图/路由器：`runtime/mission_graph.py`、`exploration_graph.py`、`mission_router.py`、`mission_pipeline_router.py`、`exploration_router.py`、`react_router.py`
- 节点：`nodes/mission_{init,decide,act,observe,eval,finalize}_node.py`、`nodes/react_{deliberate,execute,observe,finalize}_node.py`、`nodes/exploration_nodes.py`
- 测试：`test_mission_graph`、`test_exploration_graph`、`test_mission_oma_golden`、`test_react_graph_flow`、`test_mission_finalize_checkpoint`、`test_mission_decide_llm`、`test_mission_handoff`、`test_mission_steer_planning_gate`

### 修改（仅去缠绕，对活路径行为保持）
- `nodes/reasoning_or_writing_node.py` — reasoning-only 主干节点（剔除 `writing_node`/`_writing_route_allowed` 分叉）
- `services/graph_runner.py` — 派发收敛为 统一图 + supervisor
- `services/session_controller.py` — redirect/resume/confirm → 普通新一轮；status_query → 轻量状态
- `runtime/graph_cache.py`、`services/mission_executor.py`、`tests/conftest.py`

---

## 4. 重要背景：工作区原本就是“脏”的 WIP

开始重构前，工作树已有**大量未提交改动**（`router.py`、`planning_node.py`、`turn_contract.py` 及约 15 个新增文件，来自半成品的 revision-intent 方案）。

- `tests/runtime/test_router_performance.py` 的 **3 个失败属于该预存 WIP**，非本轮引入。
- **建议下一轮开始前先提交/暂存这堆 WIP**，避免新旧改动纠缠。

---

## 5. 下一轮路线（服务层批删）

阻塞点：mission/写作**服务层**被活路径**顶层 import**：
`planning_node.py`(1054L)、`turn_contract.py`(986L)、`tool_node.py`、`event_classification.py`、`session_turn.py`。

安全推进顺序：
1. 先具备**可验证手段**（LLM Key / 测试环境），能跑通 QA/工程/supervisor 活路径。
2. 重写 `planning_node`：产出 `Action` 序列；删除 mission/writing 分支与 `resolve_mission_pack`/`mission_routing`/`mission_service` 顶层依赖。
3. 重写 `turn_contract`：去 writing/mission；保留通用 turn 契约。
4. `tool_node`：接入 `Action` 执行 + `is_edit_applied` 收敛诚实契约。
5. 单一收敛门：用一个 converge 判定替换 `progress_evaluator`/`verification` 中分散的 mission/revision 逻辑。
6. 批删服务层：`mission_*`、`writing/*`、`manuscript_*`、`revision_*`、`mission_oma/*`。
7. 清 `state.py` mission/writing/revision 字段；清 `config.yaml` mission/writing 配置。
8. 删除/重写对应测试（约 335 测试文件中大量针对已删子系统）。

验收基线（需 LLM 环境）：复跑 `debug.log` 两个失败场景——“改大纲”应真正落盘改动且无空转；“缩短散文”应走 `edit_artifact(compress)`/`write_artifact` 而非 append。

---

## 6. 关键设计锚点（防止再加层）

- **识别即执行**：`Action` 自带已解析参数，禁止再引入“意图→锚点→command→edit_spec→参数”的翻译链。
- **改完即停 + 编辑诚实**：0 替换不算成功；编辑后立即收敛，不回 mission 续写。
- **单一定类**：`classify` 一次定类，禁止 `event_classification` 与 `planning` 双轨真相。
- **一个循环、一个收敛门、一个执行入口**：新增代码必须是净删除的支点，不是新的一层。

---

# 实施细则（下一轮可直接执行的文件级规格）

> 以下内容把第 5 节的路线展开为可落地的细则。每条标注落点文件、保留/删除符号、数据流。
> 所有改动遵循“先具备可验证环境 → 改活路径 → 再批删服务层”的顺序。

## A. 目标统一循环（最终形态）

```
classify(event_classification) → plan(incremental_planning)
  → act_loop:  tool_execution  ⇄  converge
  → reasoning(reasoning_or_writing) → verification → policy → output → END
（human_review / rejected / dead_letter 为旁支；memory/eval 经 close_turn_async 异步）
```

- **act_loop 单一执行入口**：`tool_execution` 执行 `Action`（含 `write_artifact`/`edit_artifact`），
  `converge` 判定是否达成 turn 目标；达成→`reasoning` 收口，未达成且有进展→回 `plan`，卡住/循环→safe finalize。
- **写作 = 通用动作**：长文写/改不再有独立 `writing_node` 生成网关，而是
  `plan` 产出 `write_artifact`(含 LLM 生成的内容) 或 `edit_artifact`(精确参数) 动作，由 `tool_execution` 落盘。

## B. `Action` 与 planning 的对接（核心契约）

- `plan` 阶段（`planning_node`）的产物从“`plan:list[str]` + `selected_tools` + `writing_intent`/`mission`”
  收敛为 **`actions: list[Action]`**（写入 `input_payload["actions"]` 或新增 state 字段 `planned_actions`）。
- `tool_execution`（`tool_node`）消费 `Action`：
  - `action.as_tool_call(task_id)` → 直接得到 `{name, params}`，无需 `command_builder`/`edit_spec` 翻译。
  - 执行后用 `is_edit_applied(result)` 判定 `edit_artifact` 是否真正改动；0 替换 → 不置成功，发起一次澄清或回 plan。
- 删除翻译链相关模块（见 D 批删清单）。

## C. 活路径大文件重写规格

### C1. `app/nodes/planning_node.py`（1054L → 目标 ~250L）
- **删除**：`mission_routing`(apply_planning_mission_decision/patch_mission_from_planning)、
  `mission_service`(init_mission_state/should_run_mission_runtime)、`mission_handoff`、`mission_steer`、
  `mission_schema`(apply_mission_step_to_payload/resolve_writing_intent_for_step)、
  `mission_intervention`、`mission.batch_unit_capability`、`packs.registry.resolve_mission_pack`、
  `task_drift` 的 `mission_rebound` 分支、`writing_step` 相关。
- **保留**：QA thin-skip（`qa_thin_skip`）、工程薄路径、LLM 结构化规划（`invoke_structured`）、
  `tool_selection`、skill planning overlay、route_audit。
- **新增**：把 LLM 规划结果映射为 `list[Action]`（answer/retrieve/read/write/edit/run_tool/run_code）。
- **state 写入**：`plan`(保留为可读摘要)、`planned_actions`、`selected_tools`、`skip_retrieval`；
  不再写 `mission`/`manuscript` 步骤。

### C2. `app/services/turn_contract.py`（986L → 目标 ~200L）
- **删除**：`contract_blocks_writing`、`materialize_writing_intent_from_contract`、
  `revision_edit_plot_contract`、`steer_replan_outline_plan`、`apply_steer_replan_outline_route`、
  `session_steer_correction_fallback_from_state`、`steer_replan_planning_fallback_from_state`、
  `_contract_from_intervention` 中 mission/steer 分支。
- **保留/简化**：`build_turn_contract`(通用 path/delivery/tools)、`validate_turn_contract_execution`、
  `is_turn_contract_fulfilled`、`contract_requires_side_effects`(用于 converge 判定 write/edit 是否必须发生)。
- 与 `Action` 对齐：契约的 `primary_op` 用 `Action.type` 表达（如 `edit_artifact`/`write_artifact`/`answer`）。

### C3. `app/nodes/tool_node.py`
- **删除**：`maybe_run_outline_edit`(outline steer)、`execute_revision_fast_path`、
  `WRITING_TOOL_NAMES`、`steer_planning_lifecycle` 接入、`revision_side_effects`。
- **新增**：统一 `_execute_action(action)` —— 读 `planned_actions` 顺序执行，artifact 动作走
  `handle_read/write/edit_text_artifact`，`run_tool` 走 registry，`run_code` 走 engineering。
- 用 `is_edit_applied` 写入 turn_facts，供 converge 判定。

### C4. 单一收敛门 `converge`（替换分散逻辑）
- **新增** `app/services/converge.py`：`evaluate_convergence(state) -> ConvergeResult(done, action, reason)`。
- **吸收并删除**：`progress_evaluator.py` 的 mission/stall/steer/revision 分支、`revision_done.py`、
  `turn_guard.py` 的 revision 分支、`runtime/revision_loop_guard.py`。
- 判定要点：
  1. turn 契约要求的 side-effect（write/edit）是否已发生且 `is_edit_applied`；
  2. 是否出现 read-loop/plan-loop（连续 N 次无新副作用）→ safe finalize；
  3. `answer` 类：reasoning 产出非空即 done。
- 落点：`route_after_tool` / `route_after_reasoning_or_writing` 改为调用 `evaluate_convergence`。

## D. 批删清单（服务层，按依赖顺序）

> 前置：C1–C4 完成且活路径 import 不再引用以下模块。

1. 写作：`services/writing/*`(全目录)、`writing_phases.py`、`writing_step.py`、`writing_contract.py`、
   `writing_delivery.py`、`writing_generation.py`、`writing_knowledge*.py`、`writing_memory.py`、
   `writing_quality.py`、`writing_reconcile.py`、`writing_stream.py`、`streaming_draft.py`、
   `confirmation/writing_delta.py`、`nodes/writing_node.py`
2. manuscript/outline/revision：`manuscript_*.py`、`outline_*.py`、`chapter_outcome.py`、
   `revision_*.py`、`confirmation/revision_boundary.py`
3. mission：`mission_executor.py`、`mission_execution.py`、`mission_orchestrator.py`、`mission_steer*.py`、
   `mission_schema.py`、`mission_service.py`、`mission_intervention.py`、`mission_routing.py`、
   `mission_supersede.py`、`mission_handoff.py`、`mission_stall*.py`、`mission_tools.py`、
   `mission_worker_lost.py`、`mission_invariants.py`、`mission_micro_reflect.py`、
   `steer_*.py`、`mission/*`(全目录)
4. mission_oma：`mission_oma/*`(全目录)
5. react：`react_loop_runner.py`、`worker_react_bridge.py`、`react_entry.py`、`react_audit.py`、
   `runtime/state` 中 react 字段、`services/runtime_router.py` 去 react_loop 推荐
6. domain：`domain/packs/writing.py`、`domain/writing_*.py`、`domain/mission.py`、
   `domain/react_loop.py`、`domain/revision_intent.py`、`domain/packs/registry.py` 去 WRITING_PACK

每删一批：`python scripts/smoke_imports.py` + 全量 import 扫描必须 0 失败。

## E. `state.py` / `agent_state_model.py` 字段清理
- 删除 `AgentState.manuscript` 字段及 `create_initial_state` 中对应初值。
- `TaskStatus` 删除：`WRITTEN`、`WRITING_FAILED`、`MISSION_RUNNING`、`MISSION_PAUSED`
  （**注意**：先全仓 grep 这些值的引用并清理，再删枚举，避免 AttributeError）。
- `merge_state` 中 `manuscript` 的嵌套合并分支一并删除。
- `agent_state_model.py` 去掉对应 typed 字段。

## F. `config/config.yaml`（840L）清理（数据层，低风险但需同步设置类）
- 删除块：`mission:`(L157, L250 两处)、`revision:`(L345)、`react_loop:`(L558)、
  `manuscript:`(L591)、`mission_micro_reflect:`(L826)、`exploration:`(L830)。
- `mode_routing:`(L452)/`mode_contracts:`(L462) 仅去 manuscript/mission/writing 条目，保留 qa/engineering/supervisor。
- 同步：`app/config/settings.py` 去掉读取以上块的属性（`MISSION_*`、`EXPLORATION_*`、`REACT_*` 等）。
- 同步 `config/config.docker.yaml`、`tests/conftest.py` 内联 config 字符串中的对应键。

## G. 测试策略（约 335 测试文件）
- 已删：8 个图/节点测试（见第 3 节）。
- 下一轮删除：`tests/services/test_mission_*`、`test_manuscript_*`、`test_writing_*`、`test_revision_*`、
  `test_outline_*`、`test_react_*`、`tests/nodes/test_writing_node*`、`tests/integration/test_revision_fast_path.py`。
- 重写：`tests/integration/test_graph_flow.py`、`test_planning_*`、`test_engineering_mode_flow.py` 对齐统一循环。
- 新增 golden（LLM-free 优先）：`Action` 已建；补 `tool_node` 执行 + converge 判定的单测。

## H. 验收基线（需 LLM/网络环境）
1. `scripts/smoke_imports.py` + 全量 import 0 失败；`pytest --collect-only` 0 错误。
2. 复跑 `debug.log` 场景：
   - “润色/修改大纲” → 真正落盘 `edit_artifact`，`replacements>=1`，无 97s→218s 空转。
   - “不要这么长（缩短散文）” → 走 `edit_artifact(compress)`/`write_artifact`，**不** append 正文。
   - “你好，你是什么模型” → `qa` 快路，无 132s 规划空耗。
3. 核心目录（services/nodes/runtime/domain）行数较起点下降 ~40%（基线 ~73k → 目标 ~40–45k）。

## I. 提交记录（已完成）
工作区原混有**预存 WIP + 本轮重构**，已按建议分两笔提交：
1. `51ff046 [fix] old problem try fix` —— 预存 revision-intent WIP。
2. `5631877 [ReFactor] stage 1` —— 本轮统一内核第一阶段。

工作树现已干净（`git status` 无未提交项），可在干净基线上按下方第 J 节 work package 推进。

---

# J. 细化执行步骤（Work Packages）

> 原则：每个 WP 独立成一笔提交；每个 WP 内每完成一步都跑**验证门**；任何一步破坏 import 立即修复或回退，不进入下一步。
> 顺序经过依赖分析：**先加法（Action 执行 + converge）→ 再重写活路径 → 最后批删服务层**。前 5 个 WP 不删服务文件，只让活路径不再引用它们。
>
> 验证门（缩写 GATE）：
> - `GATE-IMPORT`：`HF_HUB_OFFLINE=1 python scripts/smoke_imports.py` 且全量 import 扫描 0 失败。
> - `GATE-COLLECT`：`HF_HUB_OFFLINE=1 python -m pytest tests --collect-only -q` 0 错误。
> - `GATE-UNIT`：相关 LLM-free 单测全绿（`tests/domain/test_action.py` + 新增 WP 单测）。
> - `GATE-E2E`（需 LLM/网络）：`debug.log` 三场景人工复跑（见 H 节）。

全量 import 扫描脚本（建议加进 `scripts/smoke_imports.py` 或单独跑）：

```bash
HF_HUB_OFFLINE=1 python - <<'PY'
import importlib, pkgutil, app
fails=[]
for m in pkgutil.walk_packages(app.__path__, prefix="app."):
    if "__pycache__" in m.name: continue
    try: importlib.import_module(m.name)
    except Exception as e: fails.append((m.name, repr(e)))
print("FAILS:", len(fails)); [print(" ", *f) for f in fails]
PY
```

---

## WP-1 · Action 执行管线（加法，不删任何文件）

目标：让 `tool_node` 能消费 `planned_actions`，建立“识别即执行”的执行入口；planning 暂时**并行**产出 actions（旧 plan 仍在）。

1. `app/runtime/state.py`：在 `AgentState` 增加 `planned_actions: Optional[list[dict]]`，并在 `create_initial_state` 初始化为 `None`。〔GATE-IMPORT〕
2. `app/runtime/agent_state_model.py`：同步增加该 typed 字段（可选 dict 列表）。〔GATE-IMPORT〕
3. 新增 `app/services/action_executor.py`：`execute_actions(state) -> state`
   - 读 `state["planned_actions"]`，逐个 `Action.from_dict(...).as_tool_call(task_id)`；
   - artifact 动作 → `handle_read/write/edit_text_artifact`；`run_tool` → `get_tool_registry().invoke`；`run_code` → engineering；`answer`/`retrieve` 跳过（交给 reasoning/retrieval 节点）；
   - 每个结果 append 到 `tool_results`；对 `edit_artifact` 用 `is_edit_applied` 标注 `turn_facts["edit_applied"]`。
4. 新增 `tests/services/test_action_executor.py`（LLM-free）：用临时 artifact 目录验证 write→read→edit（含 0 替换 = 未应用）。〔GATE-UNIT〕
5. **暂不接线进 tool_node**（下个 WP 接），保持现状可跑。

提交：`[ReFactor] stage2-wp1: action executor (additive)`。

## WP-2 · 单一收敛门 converge（加法 + 路由切换）

目标：用一个判定替代分散的收敛逻辑；先与旧逻辑并存，由开关或直接替换路由调用。

1. 新增 `app/services/converge.py`：`evaluate_convergence(state) -> ConvergeResult(done: bool, next: str, reason: str)`。
   - 规则见 C4；输入只读 `turn_facts`/`tool_results`/`turn_contract`/`status`。
2. 新增 `tests/services/test_converge.py`（LLM-free）：构造 state 字典覆盖
   （a）edit 已应用→done；（b）edit 0 替换→not done/clarify；（c）read-loop→safe finalize；（d）answer 非空→done。〔GATE-UNIT〕
3. `app/nodes/reasoning_or_writing_node.py::route_after_reasoning_or_writing`：在返回前调用 `evaluate_convergence`，
   用其结果决定 `verification`/`incremental_planning`/`dead_letter`（保留现有 guard 作为兜底）。〔GATE-IMPORT〕〔GATE-COLLECT〕
4. `app/runtime/router.py::route_after_tool`：同样接入 converge（替换 revision_loop_guard 分支的判定来源，但暂不删该文件）。〔GATE-IMPORT〕

提交：`[ReFactor] stage2-wp2: single convergence gate`。

## WP-3 · 重写 `planning_node`（活路径，去 mission/writing）

> 需要 GATE-E2E 能力（验证 QA/工程/supervisor 仍可规划）。建议在此 WP 前确保有可用 LLM。

1. 备份阅读：通读 `app/nodes/planning_node.py`，标出 mission/writing/revision 分支区段。
2. 删除顶层 import：`mission_routing`、`mission_service`、（保留 `merge_state` 等通用）。〔逐步，GATE-IMPORT〕
3. 删除函数体内 mission 块：handoff loop、`apply_mission_step_to_payload`、`resolve_writing_intent_for_step`、`resolve_mission_pack`、`mission_rebound` 分支、`writing_step` 协调。
4. 新增映射：把 LLM 结构化规划结果 → `list[Action]`，写入 `state["planned_actions"]`；`plan` 仅保留人读摘要。
5. 保留：`qa_thin_skip`、工程薄路径、`tool_selection`、skill overlay、route_audit。
6. 新增/更新 `tests/integration/test_planning_qa_thin_skip.py` 等对齐 actions 产出（能在无 LLM 跑的断言尽量保留）。〔GATE-COLLECT〕〔GATE-E2E〕

提交：`[ReFactor] stage2-wp3: planning_node -> actions, drop mission/writing`。

## WP-4 · 重写 `turn_contract`（活路径）

1. 删除：`contract_blocks_writing`、`materialize_writing_intent_from_contract`、`revision_edit_plot_contract`、
   `steer_replan_outline_plan`、`apply_steer_replan_outline_route`、`session_steer_correction_fallback_from_state`、
   `steer_replan_planning_fallback_from_state`、`_contract_from_intervention` 的 mission/steer 分支。
2. 简化 `build_turn_contract`：`primary_op` 用 `Action.type` 词汇（answer/write_artifact/edit_artifact/run_tool/run_code）。
3. 保留：`validate_turn_contract_execution`、`is_turn_contract_fulfilled`、`contract_requires_side_effects`（供 converge）。
4. 全仓 grep 被删函数的调用点并清理（多在 `planning_node`/`graph_runner` 已删方法/`pre_planning`）。〔GATE-IMPORT〕〔GATE-COLLECT〕

提交：`[ReFactor] stage2-wp4: turn_contract generic-only`。

## WP-5 · 重写 `tool_node` 接入 Action 执行

1. 删除：`maybe_run_outline_edit`、`execute_revision_fast_path`、`WRITING_TOOL_NAMES`、`steer_planning_lifecycle`/`revision_side_effects` 接入。
2. 主体改为：若 `state["planned_actions"]` 非空 → 调 WP-1 的 `execute_actions`；否则走既有 registry staged 执行（过渡期保留）。
3. `is_edit_applied` 写 `turn_facts`，供 converge。〔GATE-IMPORT〕〔GATE-COLLECT〕〔GATE-E2E：改大纲场景应真正落盘〕

提交：`[ReFactor] stage2-wp5: tool_node executes actions`。

## WP-6 · 服务层批删（此时活路径已不再引用）

> 前置：WP-3/4/5 完成后，全量 import 扫描应显示这些模块**已无 KEEP 引用**（用 `rg -l` 复核）。每删一批后 GATE-IMPORT + GATE-COLLECT。

- 6a 写作：`services/writing/*`、`writing_phases.py`、`writing_step.py`、`writing_*.py`、`streaming_draft.py`、`confirmation/writing_delta.py`、`nodes/writing_node.py`
- 6b manuscript/outline/revision：`manuscript_*.py`、`outline_*.py`、`chapter_outcome.py`、`revision_*.py`、`confirmation/revision_boundary.py`
- 6c mission：`mission_executor.py`、`mission_execution.py`、`mission_orchestrator.py`、`mission_steer*.py`、`mission_schema.py`、`mission_service.py`、`mission_intervention.py`、`mission_routing.py`、`mission_supersede.py`、`mission_handoff.py`、`mission_stall*.py`、`mission_tools.py`、`mission_worker_lost.py`、`mission_invariants.py`、`mission_micro_reflect.py`、`steer_*.py`、`mission/*`
- 6d mission_oma：`mission_oma/*`
- 6e react：`react_loop_runner.py`、`worker_react_bridge.py`、`react_entry.py`、`react_audit.py`；`services/runtime_router.py` 去 react_loop 推荐；`runtime/revision_loop_guard.py`
- 6f domain：`domain/packs/writing.py`、`domain/writing_*.py`、`domain/mission.py`、`domain/react_loop.py`、`domain/revision_intent.py`；`domain/packs/registry.py` 去 WRITING_PACK

每批删除前先 `rg -l '<模块名>' app --glob '!**/__pycache__/**'` 确认无活引用；有则先回到 WP-3/4/5 补去缠绕。

提交：每个子批一笔，如 `[ReFactor] stage2-wp6c: delete mission services`。

## WP-7 · 状态/配置清理

1. `state.py`：删 `AgentState.manuscript` 字段 + 初值 + `merge_state` 的 manuscript 合并分支。
2. `TaskStatus`：先 `rg 'WRITTEN|WRITING_FAILED|MISSION_RUNNING|MISSION_PAUSED' app` 清理引用，再删枚举值。〔GATE-IMPORT〕
3. `agent_state_model.py`：同步删字段。
4. `config/config.yaml`：删 `mission:`/`revision:`/`react_loop:`/`manuscript:`/`mission_micro_reflect:`/`exploration:` 块；`mode_routing`/`mode_contracts` 去 manuscript/mission/writing 条目。
5. `app/config/settings.py`：删读取以上块的属性（`MISSION_*`/`EXPLORATION_*`/`REACT_*`/`MANUSCRIPT_*`）。
6. 同步 `config/config.docker.yaml` 与 `tests/conftest.py` 内联 config 字符串。〔GATE-IMPORT〕〔GATE-COLLECT〕

提交：`[ReFactor] stage2-wp7: trim state + config`。

## WP-8 · 测试清理与重写

1. 删：`tests/services/test_mission_*`、`test_manuscript_*`、`test_writing_*`、`test_revision_*`、`test_outline_*`、`test_react_*`；`tests/nodes/test_writing_node*`；`tests/integration/test_revision_fast_path.py`。
2. 重写：`test_graph_flow.py`、`test_planning_*`、`test_engineering_mode_flow.py` 对齐统一循环 + actions。
3. 补 golden：`test_action_executor.py`、`test_converge.py`（前置 WP 已建）。
4. 〔GATE-COLLECT〕 0 错误；〔GATE-E2E〕 三场景通过。

提交：`[ReFactor] stage2-wp8: test suite realign`。

---

## K. 完成定义（Definition of Done）

- [ ] 仅一套图（统一 + supervisor），路由器收敛，无 mission/exploration/react 残留模块。
- [ ] `planning_node` 产出 `Action`，`tool_node` 执行 `Action`，`converge` 单门收敛。
- [ ] 全量 import 0 失败；`pytest --collect-only` 0 错误；LLM-free 单测全绿。
- [ ] `debug.log` 三场景 GATE-E2E 通过（改大纲落盘 / 缩短不 append / QA 无 132s）。
- [ ] 核心目录（services+nodes+runtime+domain）合计 ≤ ~45k 行。
- [ ] `config.yaml`/`settings.py` 无 mission/writing/exploration/react 残留键。
