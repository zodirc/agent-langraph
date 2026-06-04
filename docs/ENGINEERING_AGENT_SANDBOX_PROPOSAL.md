# 基于意图驱动的 Agent 执行模式与安全沙箱一期方案

> 本文给出**唯一的长期工程化方案**，并允许围绕 runtime 做必要的架构重构。方案核心不是给“工程任务”补一条特例链路，而是把系统正式改造成**基于意图切换执行模式**的 Agent Runtime：系统先识别用户意图，再切换到匹配模式；模式再决定工具面、执行路径、交付形态、校验方式与安全边界。2048 只是 [`engineering_mode`](docs/ENGINEERING_AGENT_SANDBOX_PROPOSAL.md) 的验收样例，长期目标是建立统一的 **Intent → Mode → Contract → Execution** 框架。  
> **速查（路径 B vs 推理侧 `code_artifact`、模式切换）**：[`CODE_AND_ENGINEERING_PATHS.md`](CODE_AND_ENGINEERING_PATHS.md) · **会话文件工具**：[`SESSION_FILE_TOOLS.md`](SESSION_FILE_TOOLS.md) · **Web 模式切换**：`/chat` 顶栏 `interaction_mode`（见 CODE_AND_ENGINEERING_PATHS §0）

---

## 1. 问题重述

当前仓库已经有不少相关能力：

- [`docs/ROUTE_AUDIT.md`](ROUTE_AUDIT.md) 能在 planning 后纠正“代码误入手稿写作”的问题。
- [`docs/CODE_ARTIFACT_PIPELINE.md`](CODE_ARTIFACT_PIPELINE.md) 已经具备 C++ / Python 的编译校验与 `stderr` 修复闭环。
- [`docs/DISPLAY_AND_DELIVERY.md`](DISPLAY_AND_DELIVERY.md) 已经定义了 `delivery.by_kind`。
- [`docs/SESSION_TURN_POLICY.md`](SESSION_TURN_POLICY.md) 已经对 session 内新输入做了部分隔离与恢复策略。

但这些能力目前是**分散存在的**，问题在于：

1. **模式没有被正式建模**：系统仍偏向“统一 planning / reasoning 主路径”，模式切换更多是隐式行为。
2. **工程任务没有成为第一等模式**：小游戏、可编译 demo、小项目需要“落盘 → 校验 → 可预览”，这与问答/手稿本质不同，却没有独立 contract。
3. **交付和校验没有完全绑定模式**：用户明明要求“可运行”“能编译”，但系统仍可能只输出聊天代码块。
4. **会话中的意图变化处理不彻底**：如果一个 session 正在写作，用户突然要求生成 2048，本质上应切模式，而不是继续沿当前模式执行。
5. **执行增强与安全边界未在同一层表达**：如果没有统一的模式 contract，增强工具执行力时很容易把 shell 能力一起放出来。

因此，本期不是做一个“工程功能包”，而是做一次 runtime 语义收敛：

> 用“模式”作为统一的运行时抽象，把意图识别、执行路径、交付方式、校验后端和安全策略绑定在一起。

---

## 2. 一期目标

本期要落地的不是多套方案，而是同一套长期架构在一期的最小完整形态。

### 2.1 总目标

将当前 runtime 从“单主模式 + 局部纠偏”升级为“**意图驱动模式系统**”：

- 用户输入先进入意图识别；
- 系统根据意图选择目标模式；
- 模式通过 contract 约束工具、执行预算、交付方式、校验 backend 与安全边界；
- 模式切换在 trace / audit 中可观测；
- 工程类任务统一进入 [`engineering_mode`](docs/ENGINEERING_AGENT_SANDBOX_PROPOSAL.md)。

### 2.2 一期必须完成的结果

本期必须实现以下结果：

1. 新增正式的模式路由层，而不是只靠 `kind` 做后置纠偏。
2. 引入 [`Mode Contract`](docs/ENGINEERING_AGENT_SANDBOX_PROPOSAL.md) 作为运行时一等对象。
3. 把 `interactive_app` / `small_project` / `code` 三类工程意图统一收敛到 [`engineering_mode`](docs/ENGINEERING_AGENT_SANDBOX_PROPOSAL.md)。
4. 让工程模式默认走文件落盘与白名单校验链路。
5. 保持 Docker 沙箱内无任意 shell。

---

## 3. 一期设计原则

### 3.1 以模式为中心，而不是以工具为中心

问题的根因不是“缺 `write_file` 工具”，而是“系统没有决定何时应该进入工程模式”。因此本期设计必须遵循：

- 先决定模式；
- 再由模式决定可用工具；
- 再由模式决定交付与校验。

### 3.2 配置优于硬编码

延续 [`docs/ROUTE_AUDIT.md`](ROUTE_AUDIT.md) 的经验，本期所有新增机制都必须优先配置化：

- 意图识别规则
- 模式映射
- 工具白名单
- verify backend 白名单
- 执行预算
- 安全约束

### 3.3 模式切换必须可观测

如果系统在会话中“悄悄切模式”，外部无法判断当前是否真的进入工程交付路径。因此本期必须保证：

- trace 中有 `intent_kind`
- trace 中有 `target_mode`
- trace 中有 `effective_mode_contract`
- trace 中有 `mode_switch_reason`

### 3.4 安全边界优先于执行便利

本期允许增强工程执行力，但不能为了便利引入通用 shell、长驻服务、路径越界或默认联网依赖安装。

---

## 4. 目标架构

### 4.1 总体流程

```text
用户输入
  ↓
Intent Router
  ↓
intent_kind + target_mode
  ↓
Mode Registry
  ↓
加载 Mode Contract
  ├─ allowed_tools
  ├─ delivery_policy
  ├─ verify_policy
  ├─ execution_budget
  ├─ route_guards
  └─ security_policy
  ↓
执行子图 / bounded loop
  ↓
artifact / summary / audit
```

### 4.2 架构拆分

本期建议把能力拆成以下四层：

#### 第一层：Intent Router

职责：

- 识别本轮用户输入属于什么意图；
- 判断是否需要切换模式；
- 给出 `intent_kind`、`target_mode`、`confidence`、`switch_reason`。

它不是简单复用当前 `route_audit`，而是要把 [`route_audit`](docs/ROUTE_AUDIT.md) 从“规划后纠偏器”升级为“模式路由体系的一部分”。

#### 第二层：Mode Registry

职责：

- 注册所有模式；
- 按 `target_mode` 返回对应 contract；
- 管理模式的默认预算、工具面和安全边界。

#### 第三层：Mode Contract

职责：

- 定义该模式允许什么；
- 约束交付方式；
- 决定校验 backend；
- 决定执行路径和最大步数；
- 决定禁止进入哪些 route。

#### 第四层：Mode-specific execution path

职责：

- 按 contract 执行具体步骤；
- 写文件、读文件、校验、修复；
- 产出 trace、audit、artifact 和 final answer。

---

## 5. 本期模式模型

本期只正式落地三个模式，避免一次性把模式体系扩得过大。

### 5.1 [`qa_mode`](docs/ENGINEERING_AGENT_SANDBOX_PROPOSAL.md)

适用场景：

- 问答
- 解释
- 方案分析
- 技术比较

contract 特征：

- 主交付：`reasoning_summary`
- 默认不落盘
- 无 verify backend
- 可使用 retrieval / memory
- 执行步数最小

### 5.2 [`manuscript_mode`](docs/ENGINEERING_AGENT_SANDBOX_PROPOSAL.md)

适用场景：

- 长篇写作
- 手稿续写
- 章节级 mission

contract 特征：

- 主交付：writing gateway / mission artifacts
- 使用 [`docs/MANUSCRIPT_WRITING.md`](MANUSCRIPT_WRITING.md) 与 [`docs/MISSION_EXECUTION_CONTROL.md`](MISSION_EXECUTION_CONTROL.md) 既有机制
- 不得承载工程交付职责

### 5.3 [`engineering_mode`](docs/ENGINEERING_AGENT_SANDBOX_PROPOSAL.md)

适用场景：

- 2048 / 小游戏 / HTML demo
- C++ / Python 可编译 demo
- 多文件小项目
- 带构建文件的样例工程

contract 特征：

- 主交付：会话目录文件
- 默认工具：`mkdir_path`、`write_file`、`read_file`、`verify_backend`
- 主执行路径：`engineering_bounded`
- 必须绑定 verify 结果
- 必须带文件清单与预览说明
- 必须禁止进入 manuscript / mission 写作路径

---

## 6. 本期意图识别与模式路由

### 6.1 意图分类

本期只定义与当前能力边界强相关的意图：

| intent_kind | target_mode | 说明 |
|---|---|---|
| `qa` | `qa_mode` | 问答、解释、分析 |
| `manuscript` | `manuscript_mode` | 长篇、章节、续写 |
| `interactive_app` | `engineering_mode` | 浏览器游戏、HTML/JS demo |
| `small_project` | `engineering_mode` | 多文件 demo、构建工程 |
| `code` | `engineering_mode` | 单文件源码且要求编译/校验 |

### 6.2 路由规则来源

本期必须让路由规则来源于配置，而不是 Python 分支里的字符串判断。建议新增配置结构：

```yaml
mode_routing:
  by_intent:
    qa: qa_mode
    manuscript: manuscript_mode
    interactive_app: engineering_mode
    small_project: engineering_mode
    code: engineering_mode
```

并继续扩展 [`config/config.yaml`](../config/config.yaml) 和 [`config/config.docker.yaml`](../config/config.docker.yaml) 中的 `route_audit.kinds`，让它负责：

- 模式路由的特征识别；
- 与 planning 结果进行一致性校验；
- 对错误 route 做矫正。

### 6.3 模式切换时机

本期至少要支持两个切换时机：

#### 入口切换

首次接收任务时，根据当前用户目标直接选择模式。

#### 会话内切换

同一 session 中，每个新 turn 都重新判定本轮意图；如果新意图与当前模式不一致，则执行模式切换或 isolate。

这正是你强调的重点：**模式是随意图变化而变化的**。

---

## 7. [`Mode Contract`](docs/ENGINEERING_AGENT_SANDBOX_PROPOSAL.md) 设计

### 7.1 核心字段

本期 `Mode Contract` 至少包含以下字段：

```yaml
mode_contracts:
  engineering_mode:
    allowed_tools: [mkdir_path, write_file, read_file, verify_backend]
    delivery:
      primary: tool_write
      secondary: reasoning_artifacts
    execution:
      path: engineering_bounded
      max_steps: 6
      max_repair_attempts: 3
    verify:
      enabled: true
      backend_selector: by_intent
    guards:
      forbid_routes: [writing_manuscript, mission_writing]
      force_isolate_from: [manuscript_mode]
    security:
      shell_access: false
      network_access: false
      path_scope: session_root_only
```

### 7.2 设计收益

把这些配置统一到 contract 中，有三个直接收益：

1. **模式切换更彻底**：进入新模式时，工具、预算、交付和安全边界一起切换。
2. **排查更容易**：一旦交付不符合预期，可以直接从 contract 看出是路由问题还是模式实现问题。
3. **后续扩展成本更低**：未来新增模式时，只需新增 contract 与 execution path，不必在多个系统中手工补逻辑。

### 7.3 一期 contract 落地要求

本期不要求把所有历史行为都完全迁移进 contract，但以下内容必须收敛进去：

- 工具白名单
- delivery 主次策略
- verify backend 选择规则
- 最大步数与 repair 次数
- 禁止 route
- 安全策略

---

## 8. [`engineering_mode`](docs/ENGINEERING_AGENT_SANDBOX_PROPOSAL.md) 详细设计

### 8.1 为什么工程模式必须独立

工程任务的真实目标不是“获得一段代码”，而是获得**一个可交付对象**。这类对象通常具有以下特征：

- 有文件结构
- 有入口文件
- 需要写入磁盘
- 需要校验
- 需要告诉用户怎么打开/预览

这些都与 [`qa_mode`](docs/ENGINEERING_AGENT_SANDBOX_PROPOSAL.md) 或 [`manuscript_mode`](docs/ENGINEERING_AGENT_SANDBOX_PROPOSAL.md) 不同，因此必须独立。

### 8.2 工程模式子类

本期工程模式内部只分三个意图子类，不再额外扩模式：

#### `interactive_app`

适用：2048、前端小游戏、静态网页 demo。

默认交付：

- `games/<slug>/index.html`
- `games/<slug>/game.js`
- `games/<slug>/style.css`

默认 verify backend：`web_html_js`

#### `small_project`

适用：多文件小工程、包含 `Makefile` 的 demo。

默认交付：

- `projects/<slug>/src/...`
- `projects/<slug>/Makefile`
- 可选 `README.md`

默认 verify backend：`make_cpp_demo` 或后端配置指定的 project backend

#### `code`

适用：单文件代码，但明确要求“可编译”“语法正确”“通过校验”。

默认交付：

- 会话根目录单文件或最小目录结构

默认 verify backend：`cpp` 或 `python`

### 8.3 工程模式执行流程

本期 [`engineering_bounded`](docs/ENGINEERING_AGENT_SANDBOX_PROPOSAL.md) 必须固定为以下阶段：

1. **结构规划**：决定目录、文件名、入口文件。
2. **文件落盘**：调用目录创建与文件写入工具。
3. **关键文件读回**：确认内容已写入，并为 verify/repair 提供上下文。
4. **校验**：根据 `intent_kind` 选择 backend。
5. **修复**：失败时读取错误结果，受限修复。
6. **收尾**：输出文件清单、预览方式、校验结论。

### 8.4 工程模式最终回答协议

本期必须把回答格式固定化，避免再次退回“只给代码块”。

工程模式最终回答必须包含：

1. 摘要
2. 文件清单
3. 打开/预览方式
4. 校验结果
5. 若失败则说明失败状态与降级策略

### 8.5 工程模式 trace 协议

trace 中必须出现：

- `intent_kind`
- `target_mode=engineering_mode`
- `execution_path=engineering_bounded`
- `delivery_primary=tool_write`
- `verify_backend`
- `written_files[]`
- `verify_result`
- `repair_attempts`

---

## 9. 校验与修复设计

### 9.1 设计原则

校验系统必须继续遵守 [`docs/CODE_ARTIFACT_PIPELINE.md`](CODE_ARTIFACT_PIPELINE.md) 的总体原则：

1. 编译器/解释器是主裁判。
2. 校验失败时才允许有限修复。
3. 修复必须依据错误输出，不允许自由猜测反复改写。
4. 最终结果必须有明确状态：`ok` / `failed` / `degraded`。

### 9.2 一期 verify backend 清单

#### `cpp`

- 来源：复用现有 `code_artifact.backends.cpp`
- 命令模板：`g++ -std=c++17 -Wall -Wextra -c {file}`
- 适用：`intent_kind=code` 且语言为 C++

#### `python`

- 来源：复用现有 `code_artifact.backends.python`
- 命令模板：`python3 -m py_compile {file}`
- 适用：`intent_kind=code` 且语言为 Python

#### `web_html_js`

本期新增，用于 `interactive_app`：

- 必须检查 `index.html`
- 必须检查主 JS 文件存在
- 命令模板固定为 `node --check {entry_js}`
- 可增加 HTML 入口静态检查

注意：

- 本期不引入浏览器自动化测试；
- 本期不要求长期 server；
- 验收口径是“入口可预览 + JS 语法通过 + 文件结构完整”。

#### `make_cpp_demo`

本期新增，用于 `small_project`：

- 必须存在 `Makefile`
- 命令模板固定为 `make demo`
- 只允许命中预定义 target

### 9.3 backend 选择机制

backend 选择不能由模型自由指定字符串。本期建议：

1. 先由 `target_mode` 判断是否需要 verify。
2. 再由 `intent_kind + language + file layout` 决定 backend id。
3. backend id 必须存在于配置白名单中。
4. 若找不到合法 backend，则任务直接标记不可验证，而不是回退到自由 shell。

### 9.4 修复策略

修复必须有界，建议流程：

- 第 1 次校验失败：保存报告，尝试最小修复
- 第 2 次校验失败：基于错误结果做受控 LLM 修复
- 第 3 次仍失败：停止，进入 failed 或 degraded

必须避免的问题：

- 没有错误信息时也强行修
- 同一错误循环修同一处
- 修复导致文件结构漂移
- 修复过程中把原始用户目标改写掉

---

## 10. 会话内模式切换设计

### 10.1 为什么这是本期重点

如果模式切换只在任务创建时发生，那么系统依旧不灵活。真正的模式系统必须支持：

- 在同一 session 中重新识别当前输入意图；
- 根据新意图切换到更合适模式；
- 避免旧模式上下文误伤新模式执行。

### 10.2 会话切换规则

本期必须至少实现以下规则：

#### 规则 1：`manuscript_mode` → `engineering_mode`

如果当前 session 正在长文写作，但用户本轮明确要求小游戏、工程 demo、可编译程序，则必须 isolate 并切换工程模式。

#### 规则 2：`engineering_mode` → `qa_mode`

如果当前正在工程模式，但用户只是追问“这个实现为什么这样设计”，则可以转入问答模式，不必重新走落盘流程。

#### 规则 3：`qa_mode` → `engineering_mode`

如果用户最初只是问“2048 怎么实现”，后续明确说“请直接生成项目文件”，则必须切入工程模式。

### 10.3 落地方式

建议在现有 [`docs/SESSION_TURN_POLICY.md`](SESSION_TURN_POLICY.md) 对应逻辑之上新增：

- `current_mode`
- `target_mode`
- `mode_switch_action: stay | switch | isolate`
- `mode_switch_reason`

其中 `isolate` 用于避免旧模式上下文污染新模式。

### 10.4 必须避免的问题

- 因为 session 中已有活跃 mission，就禁止切工程模式
- 仅更新 prompt 文本，但执行路径并未真正切换
- 工程模式继承了 manuscript 的工具或写作参数
- 切模式后 trace/audit 不可见，导致无法排查

---

## 11. 安全沙箱设计

### 11.1 本期安全目标

本期目标不是“完全禁止执行”，而是把执行严格收敛在受控 contract 内。

因此安全设计必须回答三个问题：

1. 哪些工具能用？
2. 哪些命令能执行？
3. 哪些路径能访问？

### 11.2 命令执行边界

本期明确禁止：

- `run_shell`
- `exec(user_cmd)`
- `bash -c`
- 任意字符串拼接 subprocess

本期允许的命令只能来源于配置模板，例如：

- [`config/config.yaml`](../config/config.yaml) 中已有的 `code_artifact.backends.cpp.compile_cmd`
- 本期新增的 `project_verify.backends.web_html_js.commands`
- 本期新增的 `project_verify.backends.make_cpp_demo.commands`

### 11.3 路径边界

所有模式都必须继续受限于 session root：

- `resolve()` 后必须在会话根目录内
- 禁止 `..`
- 禁止绝对路径
- verify 使用单独 workspace
- artifact API 仅暴露当前任务目录

### 11.4 资源边界

本期必须增加以下边界：

- verify timeout
- 单文件大小上限
- 总文件数上限
- bounded loop 步数上限
- 输出日志截断
- verify backend 并发限制

### 11.5 网络边界

本期默认关闭网络相关执行：

- 不运行 `npm install`
- 不运行 `pip install`
- 不访问公网下载构建依赖

### 11.6 审计要求

必须记录以下内容：

- `target_mode`
- 使用的工具
- 写入文件列表
- backend id
- backend 执行结果
- 错误摘要
- 最终状态

---

## 12. 配置与代码落地方案

这一节回答“如何落地”。

### 12.1 配置落地

本期需要在 [`config/config.yaml`](../config/config.yaml) 与 [`config/config.docker.yaml`](../config/config.docker.yaml) 同时新增/调整：

#### A. `route_audit.kinds`

新增：

- `interactive_app`
- `small_project`

目的：为工程意图提供可配置识别依据。

#### B. `mode_routing`

新增：

```yaml
mode_routing:
  by_intent:
    qa: qa_mode
    manuscript: manuscript_mode
    interactive_app: engineering_mode
    small_project: engineering_mode
    code: engineering_mode
```

目的：让 mode switch 不再靠代码内部分支隐式推导。

#### C. `mode_contracts`

新增三类 mode contract：

- `qa_mode`
- `manuscript_mode`
- `engineering_mode`

目的：统一工具、预算、verify 与安全边界。

#### D. `project_verify`

新增 backend 配置：

- `web_html_js`
- `make_cpp_demo`

目的：承接工程模式的多文件校验需求。

### 12.2 代码结构落地

本期建议按以下代码落点推进：

#### A. 模式路由层

新增或重构为：

- `app/services/mode_router.py`
- 或在现有路由服务上增加 `resolve_target_mode()`

职责：

- 输入当前 turn goal、session 上下文、planning 结果
- 输出 `intent_kind`、`target_mode`、`switch_action`

#### B. 模式注册层

新增：

- `app/services/mode_registry.py`

职责：

- 从配置加载 mode contract
- 返回当前模式的有效 contract

#### C. 工程执行层

新增或重构：

- `app/services/engineering_execution.py`
- 或扩展现有 react / tool execution 框架，使其支持 `engineering_bounded`

职责：

- 目录结构规划
- 文件创建/写入/读取
- backend 校验
- 修复与收尾

#### D. backend 注册层

新增：

- `app/services/project_verify/`

职责：

- 维护 `web_html_js`、`make_cpp_demo` 等 backend
- 校验 backend id 合法性
- 统一执行与报告结构

### 12.3 执行图落地

本期不建议直接新增一套完全独立的大图，而是建议：

1. 保留现有 planning 主入口。
2. 在 planning 后增加 mode resolution。
3. 若 `target_mode=engineering_mode`，进入 `engineering_bounded` 子路径。
4. 若 `target_mode=manuscript_mode`，维持现有 manuscript / mission 路径。
5. 若 `target_mode=qa_mode`，维持 reasoning / retrieval 主路径。

这样落地成本更低，同时保留未来继续扩模式的空间。

---

## 13. 本期实施顺序

### P0：模式路由骨架

必须先做：

1. 定义 `target_mode`
2. 定义 `mode_contracts`
3. 打通 planning 后的 mode resolution
4. trace 输出 mode switch 结果

### P1：工程模式可用闭环

接着做：

1. 落地 `engineering_bounded`
2. 工程模式默认启用文件工具
3. 工程类主交付改为 `tool_write`
4. 最终回答固定为文件清单 + 预览方式 + 校验结果

### P2：verify 与安全闭环

再做：

1. 接入 `web_html_js`
2. 接入 `make_cpp_demo`
3. 完成 repair 上限控制
4. 完成 backend 白名单与安全测试

### P3：session 模式切换闭环

最后做：

1. 新 turn 重判 `target_mode`
2. 活跃 manuscript 会话中的工程切换
3. 完成 isolate 行为和回归测试

这个顺序的好处是：先有模式骨架，再接工程能力，最后收口 session 复杂度。

---

## 14. 本期需要重点避免的问题

这一节回答“需要避免哪些问题”。

### 14.1 伪模式切换

表现：

- trace 里说切到了 `engineering_mode`
- 但真实执行仍然是 reasoning-only
- 最终没有落盘文件

规避方式：

- 模式切换必须与 execution path 同步生效
- `target_mode` 变化时必须加载新的 contract
- `delivery_primary`、`allowed_tools`、`execution_path` 必须一起变更

### 14.2 只有 prompt 变了，代码没变

表现：

- 规划提示写了“请落盘”
- 但 runtime 仍没有工程执行路径
- 结果高度依赖模型自觉

规避方式：

- 不把模式能力只放在 prompt 里
- 工具面、verify、路径、回答模板都要在运行时硬约束

### 14.3 模式与安全边界脱钩

表现：

- 工程模式为了“方便调试”临时引入 `bash -c`
- verify backend 接受任意字符串命令

规避方式：

- backend 必须白名单
- contract 中显式 `shell_access: false`
- 所有命令模板配置化且参数受限

### 14.4 工程模式被 manuscript 语义污染

表现：

- 2048 被写入 `novel.txt`
- 工程任务触发 `mission_writing`
- `append_body`、`total_target_chars` 出现在工程路径中

规避方式：

- contract 中 `forbid_routes`
- `engineering_mode` 单独 execution path
- session 中使用 `isolate`

### 14.5 verify 失败后无限修复

表现：

- 一直修，一直编，一直循环
- 任务时长不可控

规避方式：

- 固定 `max_repair_attempts`
- 固定 `max_steps`
- 超限后进入 failed / degraded

### 14.6 Docker / 本地配置漂移

表现：

- 本地工程模式正常
- Docker 中没有相同 `mode_routing` 或 backend 配置

规避方式：

- 两份配置必须同步维护
- 增加 parity 测试校验关键块

### 14.7 artifact 可见但不可用

表现：

- 文件虽写了，但路径混乱、用户不知道怎么打开
- HTML 缺入口文件
- 构建文件与源码不匹配

规避方式：

- 工程模式回答模板固定
- 路径规则稳定
- verify 必须检查入口文件/构建文件存在性

---

## 15. 测试与验收

### 15.1 单测

必须覆盖：

- `intent_kind -> target_mode` 映射
- mode contract 加载与 fallback
- backend 选择逻辑
- 非法 backend id 拒绝
- 路径越界拒绝
- `max_steps` / `max_repair_attempts` 生效

### 15.2 集成测试

必须覆盖：

#### 2048 场景

- 进入 `engineering_mode`
- 生成 `index.html` + JS/CSS
- 调用 `web_html_js`
- 返回文件清单与预览方式

#### C++ demo 场景

- 进入 `engineering_mode`
- 源码落盘
- 调用 `cpp`
- 失败时触发 repair

#### Makefile demo 场景

- 进入 `engineering_mode`
- 生成 `Makefile`
- 调用 `make_cpp_demo`

#### session 模式切换场景

- manuscript 会话中新输入工程意图
- 正确 `isolate -> engineering_mode`

### 15.3 安全测试

必须覆盖：

- `..` 路径穿越
- 绝对路径
- 非法 backend id
- 超时中止
- 命令模板参数注入
- Docker 与本地配置一致性

### 15.4 本期验收标准

#### AC-1 模式系统生效

- trace 中可见 `intent_kind`、`target_mode`、`mode_switch_reason`
- 模式切换带来真实 execution path 变化

#### AC-2 工程模式生效

- 2048 / demo / 可编译源码统一进入 `engineering_mode`
- 主交付物为文件而非聊天代码块

#### AC-3 校验闭环生效

- `cpp` / `python` / `web_html_js` / `make_cpp_demo` 可用
- 失败时有受控 repair

#### AC-4 会话切换生效

- 活跃写作会话中可切换到工程模式
- 不误 resume 手稿路径

#### AC-5 安全边界生效

- 无任意 shell
- 路径隔离有效
- backend 白名单有效

---

## 16. 结论

本期真正要落地的，不是一个“2048 专用能力”，而是一个长期有效的 runtime 重构方向：

- **先识别意图**
- **再切换模式**
- **由模式 contract 统一约束工具、交付、校验与安全**
- **工程任务统一进入 [`engineering_mode`](docs/ENGINEERING_AGENT_SANDBOX_PROPOSAL.md)**

在这套架构下，2048 只是一个验收样例；真正建立起来的是系统的模式灵活性。未来无论是小游戏、demo、可编译程序还是更多类型的受控执行任务，都可以沿着同一套 **Intent Router → Mode Registry → Mode Contract → Execution Path** 框架扩展，而不必继续在单一模式里累积越来越多的补丁逻辑。
