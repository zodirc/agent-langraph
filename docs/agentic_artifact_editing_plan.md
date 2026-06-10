# 实施文档：让 Agent 像 Cursor 一样自然地编辑已有文件

> 目标：把"操作已有产物"（润色/修改/改写/重写/继续）从"分类成闲聊 → 只描述不动手"，
> 改成"统一 Agent 循环 + 工具常驻 + 模型驱动动作"，对齐 Cursor/Auto 的自然编辑体验，
> 同时保留本项目已有的 `turn_facts` 诚实性校验与收敛闸。

状态：**已实施**（Phase 1–3，2026-06）
影响层：控制循环与规划层（L1）、上下文工程层、场景策略层（mode contract）

> 架构总览见 `docs/arch.md` §2.8、§4.2、§4.4、§5.1–5.2。

---

## 1. 问题陈述

### 1.1 现象

用户在已经生成 `北平的车辙_散文.txt` 后输入"你重新试试这个润色"。系统**理解了**要做什么
（推理流里明确写出"读文件 → 润色 → 写回"），但**没有执行任何文件动作**，最终只回了一段
描述性文字。

实测 `debug.log`（turn 6）关键投影：

```
planning/plan · planning: qa_thin_skip
planning/plan · 1. respond directly
planning/plan_effective · 工具: (无)
mode_resolution · target_mode: qa_mode   contract: ... tools=[]
route_audit · 计划路由: reasoning_only aligned=True
```

### 1.2 本质

这是**路由/能力发放**问题，不是模型能力问题：短句改写请求在入口被分类为"会话式 QA"，
进入 `qa_mode`，而 `qa_mode` 的契约把工具集清空。于是规划只产出一个 `answer` 动作，
`tool_execution` 阶段无事可做，推理节点只能"复述计划"。

---

## 2. 根因定位（精确到代码）

调用链（从入口到"无工具"）：

1. `app/services/interaction_goal.py` → `goal_is_conversational_qa()`
   末尾兜底 `if len(text) <= 16 and not _ENGINEERING_SIGNAL_RE.search(text): return True`。
   "你重新试试这个润色"仅 9 字，且"润色"不在 `_ENGINEERING_SIGNAL_RE`（只有 落盘/生成/编译/项目/源码…），
   于是被判为闲聊。

2. `app/services/pre_planning.py` → `should_skip_qa_planning_llm()`
   `target_mode == "qa_mode"` 且 `goal_is_conversational_qa()` 为真 → 返回 True。

3. `app/nodes/planning_node.py` → QA thin 分支（L194-226）
   产出 `plan=["respond directly"]`、`planned_actions=[Action(type="answer")]`、`tools=[]`、
   `thin_execution_profile="qa_direct"`。

4. `app/services/mode_resolution.py` → `apply_mode_contract_to_state()`
   对 `qa_mode` 调 `apply_qa_mode_contract()`（`app/services/mode_execution.py` L50-75），
   `strip_tools_for_mode(tools, allowed=frozenset())` 把工具清空，并禁用 `writing_intent`。

5. 配置 `config/config.yaml`：
   - `mode_routing.by_intent`: `qa: qa_mode`、`retry_recovery: qa_mode`、`general: qa_mode`
   - `mode_contracts.qa_mode.allowed_tools: []`  ← **工具牢笼**
   - `route_audit.kinds.manuscript.patterns` 只有 `小说|章节|大纲|续写|万字`，**不含"润色/修改/改写"**

6. `app/services/route_audit/inference.py` → `_auto_switch_primary_kind()`
   只有在 `writing_intent_enabled AND (manuscript_body_exists | mission_writing)` 时才会把
   qa 提升为 manuscript；而 `manuscript_body_names = [novel.txt, body.txt]`，
   用户文件名是 `北平的车辙_散文.txt`，`manuscript_body_exists=False` → 不提升。

补充事实：`mode_contracts` 里**没有定义 `manuscript_mode`**，所以
`get_mode_contract("manuscript_mode")` 返回 `None`，`apply_mode_contract_to_state()` 直接
原样返回——也就是说，只要意图被判为 `manuscript`，工具就**不会被剥夺**。这给了我们一条
低风险的修复路径（见 Phase 1）。

---

## 3. 设计原则（对齐 Cursor / Auto）

| 维度 | Cursor/Auto 的做法 | 本项目现状 | 目标 |
|---|---|---|---|
| 能力发放 | 工具常驻，模型在循环里自己调 | 入口分类决定工具集 | 工具常驻，模式只做偏置 |
| "模式" | 粗粒度权限闸（Agent 可改 / Ask 只读） | 细粒度工具白名单（qa_mode=[]） | 粗粒度权限 + 风险闸 |
| "Auto" | 只选模型，不砍能力 | thin-skip 砍工具 | thin-skip 仅省延迟，保留工具能力 |
| 指代消解 | 模型看得到当前文件 | 推理上下文无产物清单 | manifest 进规划/推理上下文 |
| 诚实性 | 工具结果即真相 | 已有 `turn_facts` 校验 | **保留**，作为差异化优势 |

核心一句话：**把"模式决定能力"改成"模式只做偏置、能力默认常驻、由模型在循环里决定动作"。**

---

## 4. 目标架构

```
用户输入
  └─ event_classification（不变）
  └─ pre_planning：意图观测 → 模式偏置（不再据此清空工具）
  └─ planning：
        ├─ 纯寒暄/确认 → thin answer（无工具，省延迟）   ← 仅限真闲聊
        └─ 其它（含"改已有文件"）→ Agent 规划：
              产出 read_artifact / edit_artifact / write_artifact 动作
              （上下文里带 artifact_manifest，"这个"可被消解）
  └─ tool_execution：执行动作（已支持）
  └─ reasoning：基于 turn_facts 诚实复述真实 diff（已支持）
  └─ converge / verification：收敛闸（已支持）
```

---

## 5. 分阶段实施

按"影响面从小到大、风险从低到高"分三阶段。Phase 1 即可让"润色一下"真正读写文件。

---

### Phase 1（低风险，最小改动）：让"改已有产物"不被误判为闲聊，并能拿到文件

目标：短句改写请求不再走 `qa_mode` 无工具直答，而是走带工具的规划路径，且规划/推理能看到
当前产物清单。

#### 1.1 新增"产物编辑意图"识别

新增文件 `app/services/artifact_edit_intent.py`：

```python
"""Detect 'edit an existing artifact' intent (polish/revise/rewrite/continue)."""

from __future__ import annotations

import re
from typing import Any

# 改写类动词（指向"对已有内容再加工"，而非新建/纯问答）
_EDIT_VERB_RE = re.compile(
    r"(?i)(润色|修改|改写|重写|改一下|改改|调整|优化|精简|扩写|续写|接着写|"
    r"重新写|再写|改成|换个|重来|重新试|再试|polish|revise|rewrite|refine|edit|improve)"
)
# 指向"已有产物"的指代
_REFERS_PRIOR_RE = re.compile(
    r"(?i)(这个|那个|这篇|那篇|刚才|之前|上面|上一[篇个版]|它|刚写的|the file|that file|previous|above)"
)


def has_existing_artifacts(state: dict[str, Any]) -> bool:
    """True when the session already has at least one artifact on disk."""
    from app.services.artifact_resolver import build_artifact_manifest

    task_id = str(state.get("task_id") or "")
    return bool(task_id) and bool(build_artifact_manifest(task_id))


def is_artifact_edit_goal(goal: str) -> bool:
    text = (goal or "").strip()
    if not text:
        return False
    if not _EDIT_VERB_RE.search(text):
        return False
    # 含改写动词即可；指代是加分项，但不强制（"润色一下"也算）
    return True


def detect_artifact_edit_intent(state: dict[str, Any], goal: str) -> bool:
    """Edit intent is only actionable when artifacts exist to edit."""
    return is_artifact_edit_goal(goal) and has_existing_artifacts(state)
```

> 说明：用 `has_existing_artifacts()` 把"有没有可改的文件"作为前置条件，避免把"帮我润色这段话"
> 这种没有产物的请求也强行拉进工具路径。

#### 1.2 短句兜底不再吞掉改写意图

修改 `app/services/interaction_goal.py` 的 `goal_is_conversational_qa()`，在 `len<=16` 兜底前
排除改写动词：

```python
def goal_is_conversational_qa(goal: str) -> bool:
    text = (goal or "").strip()
    if not text:
        return True
    if _CONVERSATIONAL_QA_RE.match(text):
        return True
    if _EXPLICIT_DELIVERY_RE.search(text):
        return False
    if _ENGINEERING_SIGNAL_RE.search(text):
        return False
    # NEW: 改写类动词不是闲聊（即使很短）
    from app.services.artifact_edit_intent import _EDIT_VERB_RE
    if _EDIT_VERB_RE.search(text):
        return False
    if _QA_FOLLOWUP_RE.search(text):
        return True
    if len(text) <= 16 and not _ENGINEERING_SIGNAL_RE.search(text):
        return True
    return False
```

> 注意：`goal_is_conversational_qa` 仅依据文本（无 state），所以这里只排除"改写动词"，
> "是否真有产物"留给规划路径用 `detect_artifact_edit_intent(state, goal)` 判定。

#### 1.3 规划阶段：编辑意图绕开 QA thin-skip，走带工具的薄规划

在 `app/nodes/planning_node.py`，QA thin 分支之前插入"产物编辑薄路径"。它产出
`read_artifact` + `edit_artifact` 动作，让 `tool_execution` 真正执行。

先在 `app/services/pre_planning.py` 增加一个薄计划构造器：

```python
def artifact_edit_thin_actions(state: AgentState) -> list["Action"]:
    """read → edit for the single resolvable artifact (Cursor-like in-place edit)."""
    from app.domain.action import Action
    from app.services.artifact_resolver import build_artifact_manifest

    task_id = str(state.get("task_id") or "")
    manifest = build_artifact_manifest(task_id)
    # 单文件：可确定目标；多文件：交给 LLM 规划具名（见 Phase 2），此处只兜单文件
    filename = manifest[0].filename if len(manifest) == 1 else ""
    if not filename:
        return []
    return [
        Action(type="read_artifact", params={"filename": filename}, source="structural"),
        Action(
            type="edit_artifact",
            params={"filename": filename},   # old_text/new_text 由 reasoning/工具阶段补全
            completes_turn=True,
            source="structural",
        ),
    ]
```

> 现实约束：`edit_text_artifact` 需要 `old_text/new_text` 或整篇替换。最稳妥的"润色"语义其实是
> **读出原文 → 模型生成润色全文 → `write_text_artifact` 覆盖写回**（而非局部 diff）。因此 Phase 1
> 推荐用 `read_artifact` + `write_artifact(overwrite)` 组合，避免 `edit` 需要精确 old_text 的脆弱性：

```python
        Action(type="read_artifact", params={"filename": filename}, source="structural"),
        Action(
            type="write_artifact",
            params={"filename": filename, "mode": "overwrite"},  # content 由 reasoning 产出
            completes_turn=True,
            source="structural",
        ),
```

在 `planning_node.py` 接入（QA thin 分支**之前**）：

```python
        from app.services.artifact_edit_intent import detect_artifact_edit_intent

        goal = str(payload.get("goal") or payload.get("query") or "").strip()
        if detect_artifact_edit_intent(state, goal):
            from app.services.pre_planning import artifact_edit_thin_actions
            actions = artifact_edit_thin_actions(state)
            if actions:
                exec_tools, tool_params, stages = _execution_transport_from_actions(actions)
                payload["tool_params"] = {**payload.get("tool_params", {}), **tool_params}
                payload["tool_stages"] = stages
                payload["thin_execution_profile"] = "artifact_edit"
                plan = ["读取已有产物", "生成修订内容", "写回文件"]
                report_plan_trace(plan, exec_tools, meta={"planning": "artifact_edit_thin"})
                return _finish_thin(
                    state, payload=payload, plan=plan, tools=exec_tools,
                    planned_actions=actions, audit_action="artifact_edit_thin",
                    audit_detail={"target_mode": payload.get("target_mode"), "goal_preview": goal[:80]},
                    pin_mode=False,   # 不要把 mode 钉死在 qa_mode
                )
```

> 关键：`pin_mode=False`，否则 `_finish_thin` 会把 `target_mode` 重新钉回 `qa_mode`，导致后续
> mode contract 再次清空工具（见下一条）。

#### 1.4 让这条路径的工具不被 mode contract 清空

`apply_mode_contract_to_state()` 对 `qa_mode` 会清空工具。两种处理：

- 简单法（Phase 1 推荐）：在 `apply_qa_mode_contract()` 增加豁免——当
  `payload.get("thin_execution_profile") == "artifact_edit"` 时，保留产物读写工具：

```python
# app/services/mode_execution.py
_ARTIFACT_TOOLS = frozenset(
    {"read_text_artifact", "write_text_artifact", "append_text_artifact", "edit_text_artifact"}
)

def apply_qa_mode_contract(state, payload, audit, tools, intent):
    profile = str(payload.get("thin_execution_profile") or "")
    if profile == "artifact_edit":
        # 产物编辑：保留产物工具，仅剥离工程/其它工具
        kept = [t for t in tools if t in _ARTIFACT_TOOLS]
        audit["writing_blocked"] = False
        payload["writing_intent"] = {**intent, "enabled": True, "source": "artifact_edit"}
        return payload, audit, kept, merge_state(state, execution_mode="single")
    # ...原逻辑（清空工具）保持不变...
```

- 结构法（Phase 3）：见第 7 节，从配置层让 qa_mode 不再 `allowed_tools: []`。

#### 1.5 把产物清单喂进推理上下文（自然指代）

规划侧已经通过 `manifest_for_planning()` 注入了 `artifact_manifest`（见 `planning_node.py` L300）。
推理侧 `app/services/fact_layer.py::reasoning_context_from_state()` 目前**没有**产物清单。补上：

```python
# app/services/fact_layer.py，reasoning_context_from_state 返回值里增加：
from app.services.artifact_resolver import build_artifact_manifest
...
    return {
        ...,
        "artifact_manifest": [
            e.to_dict() for e in build_artifact_manifest(str(state.get("task_id") or ""))
        ],
    }
```

这样推理在"生成润色全文"时知道原文件名与体量，写回 `write_artifact` 时 `content` 有依据。

#### 1.6 Phase 1 测试

新增 `tests/services/test_artifact_edit_intent.py`：

- `is_artifact_edit_goal("你重新试试这个润色")` → True
- `is_artifact_edit_goal("你好")` → False
- `goal_is_conversational_qa("你重新试试这个润色")` → False（回归 1.2）
- `goal_is_conversational_qa("你好")` → True（不回归）

新增 `tests/integration/test_planning_artifact_edit.py`（mock 磁盘单产物 + LLM 关闭）：

- 构造 `task_id` 下存在 `北平的车辙_散文.txt`，goal="润色一下"
- 跑 `planning_node` → 断言 `planned_actions` 含 `read_artifact` 且含 `write_artifact`，
  `selected_tools` 含 `read_text_artifact`/`write_text_artifact`，`target_mode != qa_mode` 锁死无工具。

---

### Phase 2（中风险）：多文件消解 + thin-skip 工具感知 + LLM 规划兜底

Phase 1 只兜"单文件"。Phase 2 处理多产物与更自然的循环。

#### 2.1 多文件：交给 LLM 规划具名

当 `len(manifest) > 1` 时，不走结构化薄路径，而是进入**正常 LLM 规划**，但：

- 在 `build_planning_system_prompt`（`app/config/prompts.py`）中加入指令：
  "当用户要求修改/润色已有产物且 `artifact_manifest` 非空时，必须输出
  `read_artifact` + `write_artifact`/`edit_artifact` 动作并指定 `filename`。"
- `artifact_manifest` 已在规划上下文中（`planning_node.py` L300），模型可据此选择文件。

#### 2.2 thin-skip 工具感知（统一原则）

将"是否 thin-skip"从"只看 goal 文本长度"改为"看是否需要工具"。在
`should_skip_qa_planning_llm()` 增加保护：

```python
def should_skip_qa_planning_llm(state: AgentState) -> bool:
    payload = state.get("input_payload") or {}
    if not payload.get("pre_planning_completed"):
        return False
    if str(payload.get("target_mode") or "") != "qa_mode":
        return False
    if planning_must_run_llm(state):
        return False
    goal = str(payload.get("goal") or payload.get("query") or "").strip()
    # NEW: 任何"需要碰文件"的意图都不许 thin-skip
    from app.services.artifact_edit_intent import detect_artifact_edit_intent
    if detect_artifact_edit_intent(state, goal):
        return False
    from app.services.interaction_goal import goal_is_conversational_qa
    return goal_is_conversational_qa(goal)
```

#### 2.3 编辑诚实性（防止"假装改了"）

`edit_text_artifact` 已返回 `replacements`/`diff_preview`；`write_text_artifact` 返回 `bytes`。
利用已有 `turn_contract.validate_turn_contract_execution()`：

- 若 `primary_op == "edit_artifact"` 且 `turn_facts.edit_applied is False` → 记
  `contract_edit_not_applied`，触发 converge replan（已实现，见 `turn_contract.py` L166-169）。
- Phase 2 增强：覆盖写回（overwrite）时，断言 `bytes` 与原文件不同或显著变化，避免空写。

#### 2.4 Phase 2 测试

- 多产物下 goal="把散文那篇润色一下" → LLM 规划（mock）产出带 `filename` 的 write/edit 动作。
- thin-skip 工具感知：goal="润色一下" + 有产物 → `should_skip_qa_planning_llm` False。

---

### Phase 3（结构性，最高价值）：解除 qa_mode 工具硬封锁，模式降级为偏置

这是真正对齐 Cursor 的一步：**能力常驻，模式只调默认行为/风险，不再清空工具集。**

#### 3.1 配置层

`config/config.yaml`：

```yaml
mode_contracts:
  qa_mode:
    # 不再清空；产物读写常驻，仅默认不主动写（由动作驱动）
    allowed_tools: [read_text_artifact, write_text_artifact, append_text_artifact, edit_text_artifact, calculator, grep_text, get_runtime_info]
    delivery:
      primary: reasoning_summary
    execution:
      path: reasoning
      max_steps: 4
```

> 风险点：`allowed_tools` 从空集变为常驻集后，`apply_qa_mode_contract()` 的"清空"语义要同步
> 改为"按 allowlist 过滤"，否则会与配置打架。

#### 3.2 代码层

`app/services/mode_execution.py::apply_qa_mode_contract()`：
从"无条件清空 + 禁用写意图"改为"按 contract.allowed_tools 过滤 + 写意图由动作决定"：

```python
def apply_qa_mode_contract(state, payload, audit, tools, intent, *, allowed: frozenset[str]):
    kept = strip_tools_for_mode(tools, allowed=allowed, forbid_engineering=True)
    audit["writing_blocked"] = False        # 不再硬封锁
    payload["writing_intent"] = {**intent, "enabled": bool(kept), "source": "qa_mode_resident"}
    return payload, audit, kept, merge_state(state, execution_mode="single")
```

调用处 `apply_mode_contract_to_state()` 把 `contract.allowed_tools` 传入。

#### 3.3 路由简化（可选）

一旦工具常驻，`mode_routing.by_intent` 里 `qa/general/retry_recovery → qa_mode` 不再意味着
"无能力"，只意味着"默认偏向回答、低步数预算"。`manuscript_mode` 可正式补一份 contract
（当前缺失），统一三种模式的语义。

#### 3.4 Phase 3 测试 + 回归

- 全量回归：`tests/services/test_mode_*`、`test_thin_execution.py`、`test_turn_contract*.py`、
  `test_planning_qa_thin_skip.py`、`test_pre_planning_qa_thin.py`。
- 新增：qa_mode 下 goal="润色一下" + 有产物 → 工具不为空、能产出 write 动作。
- 守护：纯寒暄"你好" → 仍 thin answer、无工具、低延迟（防止 Phase 3 把闲聊也变重）。

---

## 6. 改动文件清单（汇总）

| 阶段 | 文件 | 改动 |
|---|---|---|
| P1 | `app/services/artifact_edit_intent.py`（新） | 编辑意图识别 |
| P1 | `app/services/interaction_goal.py` | 短句兜底排除改写动词 |
| P1 | `app/services/pre_planning.py` | `artifact_edit_thin_actions()` |
| P1 | `app/nodes/planning_node.py` | 接入产物编辑薄路径（QA thin 之前） |
| P1 | `app/services/mode_execution.py` | `artifact_edit` profile 豁免工具清空 |
| P1 | `app/services/fact_layer.py` | 推理上下文注入 `artifact_manifest` |
| P2 | `app/config/prompts.py` | 规划提示：有产物+改写→必须 read+write 动作 |
| P2 | `app/services/pre_planning.py` | `should_skip_qa_planning_llm` 工具感知 |
| P2 | `app/services/turn_contract.py` | 覆盖写回的诚实性增强 |
| P3 | `config/config.yaml` | qa_mode `allowed_tools` 常驻 |
| P3 | `app/services/mode_execution.py` | 清空→allowlist 过滤 |
| P3 | `app/services/mode_registry.py`/config | 补 `manuscript_mode` 契约（可选） |

---

## 7. 兼容性、灰度与回滚

- **配置开关**：Phase 1/2 用 `payload.thin_execution_profile == "artifact_edit"` 与新意图
  检测控制，默认开启但作用域窄（仅"改写动词 + 有产物"）。可加 `settings.ARTIFACT_EDIT_FAST_PATH`
  布尔开关，便于一键回滚。
- **Phase 3** 改动面大，建议加 `settings.QA_MODE_TOOLS_RESIDENT`（默认 False），灰度验证后再翻默认。
- **回滚**：Phase 1/2 删除新分支即可恢复旧行为；Phase 3 由配置/开关控制，零代码回滚。

---

## 8. 指标与可观测

复用 `get_metrics_service().inc_contract_event(...)`，新增计数：

- `artifact_edit_fast_path` —— 走了产物编辑薄路径的次数
- `artifact_edit_resolved_single` / `artifact_edit_ambiguous_multi`
- `artifact_edit_write_verified` —— 写回确有字节变化
- 复用 `unfulfilled` —— 契约声明改文件但 turn_facts 无写入（诚实性失败）

审计：薄路径在 `_finish_thin` 已写 `audit_action="artifact_edit_thin"`，便于 `/audit <task_id>` 复盘。

---

## 9. 验收标准（端到端）

1. 已有 `北平的车辙_散文.txt` 时输入"你重新试试这个润色"：
   - 计划含"读取/写回"，`selected_tools` 含 `read_text_artifact`+`write_text_artifact`
   - `tool_execution` 真正产生一次写回（`turn_facts.tools_executed` 有 write 记录）
   - 推理 summary 基于真实 diff/字节，不出现"我将要…"的未来式空话
2. 纯寒暄"你好" / "谢谢"：仍走 thin answer，无工具，延迟不退化。
3. 无产物时"帮我润色这段：…"：走正常推理（不强行造工具动作）。
4. 全量既有测试通过；新增 P1 单测 + 集成测试通过。

---

## 10. 备注：为什么这是"更合理"的做法

- **能力与意图解耦**：模型永远能用工具，要不要用由它在循环里判断，而不是入口一次性猜死——
  这正是 Cursor Agent"自然"的来源。
- **保留诚实闸是本项目的差异化**：裸 Agent 循环容易"嘴上说改了实际没改"；本项目的
  `turn_facts` + `converge` 能保证"说到做到"，应当保留并强化，而不是因为追求自然而丢弃。
- **渐进式**：P1 就能解决用户当前痛点，P3 才触碰结构；每步都有开关和回滚，符合
  `arch.md` 所述"可扩展、可恢复、可观测"的设计取向。
```
