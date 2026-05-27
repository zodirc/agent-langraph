# 开发规范文档

> **本文档是面向开发模型（Code Mode）的强制性规范。**
> 在实现本仓库 LangGraph Runtime 时，必须严格遵守以下所有规则（架构以 `app/` 目录与 [`README.md`](README.md) 为准）。

---

## 1. 核心原则

### 1.1 禁止模拟实现

**这是最高优先级规则。**

以下行为被严格禁止：

- ❌ 创建只有函数签名而函数体为 `pass` 或 `...` 的文件
- ❌ 在函数体中写 `# TODO: implement this` 然后留空
- ❌ 返回硬编码的假数据（如 `return {"status": "ok"}`）而不做真实逻辑
- ❌ 用 `raise NotImplementedError()` 占位
- ❌ 创建空的测试文件只写 `def test_placeholder(): pass`

**正确做法：**

- ✅ 每个函数必须包含完整的业务逻辑实现
- ✅ 如果某个功能依赖外部服务（如 LLM），必须实现真实的调用逻辑
- ✅ 如果某个功能暂时无法完成（如缺少 API Key），应在代码中实现完整逻辑，通过配置文件控制是否启用
- ✅ 测试必须包含真实的断言和验证逻辑

### 1.2 遵循架构文档

所有开发必须严格遵循本仓库现有图结构与 `AgentState` 约定（见 `app/runtime/state.py`）。  
长文手稿（Manuscript + Writing 节点）另见 [`docs/MANUSCRIPT_WRITING.md`](docs/MANUSCRIPT_WRITING.md)。

- 目录结构必须与文档第5章一致
- State 对象必须与文档第6.1节的 `AgentState` TypedDict 一致
- Graph 构建必须与文档第6.3节的 `build_agent_graph()` 一致
- Router 逻辑必须与文档第6.4节一致
- 节点职责必须与文档第7章一致
- 配置管理必须与文档第25.9节的 `Settings` 类一致

### 1.3 增量开发

按照文档第16章的分阶段实施路线开发：

1. **阶段1**：最小闭环（Planning → Retrieval → Tool Execution → Reasoning → Policy → Output → Memory Writeback）
2. **阶段2**：引入工具与策略
3. **阶段3**：引入长期记忆与人工审核
4. **阶段4**：支持多入口与批量任务
5. **阶段5**：扩展多领域能力包

每个阶段完成后，所有接口测试必须通过。

---

## 2. 接口测试规范

### 2.1 测试目录结构

必须建立以下测试目录，覆盖骨架的所有接口：

```text
tests/
  api/                          # API 接口测试
    test_task_api.py            # 任务创建、查询、结果获取
    test_review_api.py          # 人工审核提交与恢复
  nodes/                        # 节点接口测试
    test_planning_node.py       # Planning Node 输入输出验证
    test_retrieval_node.py      # Retrieval Node 输入输出验证
    test_tool_node.py           # Tool Execution Node 输入输出验证
    test_reasoning_node.py      # Reasoning Node 输入输出验证
    test_policy_node.py         # Policy Node 所有路径覆盖
    test_human_review_node.py   # Human Review 暂停/恢复验证
    test_output_node.py         # Output Node 输出格式验证
    test_memory_writeback_node.py  # Memory Writeback 写入验证
  services/                     # 服务层接口测试
    test_llm_client.py          # LLM 调用与重试验证
    test_tool_registry.py       # 工具注册与调用验证
    test_policy_engine.py       # 策略引擎裁决验证
    test_memory_store.py        # 记忆存储读写验证
    test_state_store.py         # 状态持久化验证
    test_knowledge_store.py     # 知识检索验证
  integration/                  # 集成测试
    test_graph_flow.py          # 完整图流转测试
    test_state_persistence.py   # 状态持久化与恢复测试
    test_error_recovery.py      # 错误恢复与重试测试
  conftest.py                   # 共享 fixtures
```

### 2.2 测试编写要求

#### 每个节点测试必须验证：

1. **输入状态正确性** — 节点接收到的 `AgentState` 包含预期字段
2. **输出状态正确性** — 节点返回的 `AgentState` 中目标字段已被正确更新
3. **状态流转正确性** — `status` 和 `current_node` 字段已正确变更
4. **审计日志追加** — `audit_log` 中新增了本节点的执行记录
5. **错误处理** — 异常情况下 `errors` 列表被正确追加

#### API 测试必须验证：

1. **创建任务** — POST 请求返回 `task_id`，状态为 `NEW`
2. **查询状态** — GET 请求返回当前 `status`、`current_node`
3. **查询结果** — 任务完成后返回 `final_answer`、`structured_output`、`artifacts`
4. **提交审核** — POST 审核结果后任务恢复执行
5. **错误响应** — 无效请求返回正确的 HTTP 错误码和错误信息

#### 集成测试必须验证：

1. **完整流转** — 从 `NEW` 到 `COMPLETED` 的完整路径
2. **策略拦截** — 高风险任务被正确拦截进入 `WAITING_REVIEW`
3. **错误恢复** — 节点失败后正确重试或进入死信队列
4. **状态持久化** — 任务中断后可从持久化存储恢复

### 2.3 测试运行

```bash
# 运行所有测试
pytest tests/ -v

# 运行特定层测试
pytest tests/nodes/ -v
pytest tests/api/ -v
pytest tests/integration/ -v

# 运行带覆盖率报告
pytest tests/ --cov=app --cov-report=html
```

### 2.4 性能与加速路径

- **规划**：仅 LLM（`planning_node` → `invoke_structured`）；工具由模型在 JSON 里按需选择。
- **快速推理**（`app/services/fast_reasoning.py`，默认关闭）：仅 `calculator` 等确定性结果可跳过推理 LLM；`get_runtime_info` 必须走推理 LLM。
- **Supervisor 分解**：`decompose_task` 由 LLM 根据 `build_worker_catalog()` 选 domain，无关键词 `infer_domain`。
- **路由**（`skip_retrieval` / `skip_retrieval_when_no_tools`）：无检索需求时跳过 `retrieval` 节点。
- **LLM**：`model.max_tokens_by_purpose` 按节点限制输出长度；`get_llm()` 使用统一 `MODEL_NAME` 并缓存客户端实例。
- **SSE**：`tool_preview`、`answer_preview` 事件用于 Web CLI 提前展示中间结果。
- 新增性能相关逻辑时，补充 `tests/services/test_fast_*.py` 与 `tests/runtime/test_router_performance.py`。

### 2.5 测试覆盖率要求

| 层次 | 最低覆盖率 |
|------|-----------|
| nodes/ | 90% |
| services/ | 85% |
| api/ | 80% |
| runtime/ | 95% |

---

## 3. 代码规范

### 3.1 文件命名

- Python 文件：`snake_case.py`
- 测试文件：`test_<被测模块名>.py`
- 配置文件：`config.yaml`、`.env`

### 3.2 类型注解

所有函数必须包含完整的类型注解：

```python
# ✅ 正确
def planning_node(state: AgentState) -> AgentState:
    ...

# ❌ 错误
def planning_node(state):
    ...
```

### 3.3 节点函数签名

所有节点函数必须遵循统一签名：

```python
def <node_name>_node(state: AgentState) -> AgentState:
    """
    节点职责描述。

    读取字段：state.input_payload, state.plan, ...
    写入字段：state.plan, state.status, state.current_node, state.audit_log
    """
    # 1. 从 state 读取输入
    # 2. 执行核心逻辑
    # 3. 构造审计记录
    # 4. 返回更新后的 state
```

### 3.4 错误处理

每个节点必须包含错误处理，不允许未捕获异常中止整个图：

```python
def tool_execution_node(state: AgentState) -> AgentState:
    try:
        # 核心逻辑
        result = execute_tool(state["selected_tools"])
        return {
            **state,
            "tool_results": result,
            "status": "TOOL_EXECUTED",
            "current_node": "tool_execution",
            "audit_log": state["audit_log"] + [{"node": "tool_execution", "action": "success"}],
        }
    except Exception as e:
        return {
            **state,
            "errors": state["errors"] + [f"tool_execution: {str(e)}"],
            "retry_count": state["retry_count"] + 1,
            "status": "TOOL_FAILED",
            "audit_log": state["audit_log"] + [{"node": "tool_execution", "action": "error", "detail": str(e)}],
        }
```

### 3.5 配置管理

- 所有配置通过 `app/config/settings.py` 的 `Settings` 实例访问
- 敏感信息（API Key）只通过环境变量注入
- 禁止在代码中硬编码任何密钥或 URL

---

## 4. 开发流程

### 4.1 每个模块的开发顺序

对于每个模块，必须按以下顺序开发：

1. **先写测试** — 根据架构文档定义的接口编写测试用例
2. **再写实现** — 实现完整的业务逻辑，使测试通过
3. **最后验证** — 运行测试确认通过，检查覆盖率

### 4.2 提交检查清单

每次完成一个模块后，必须确认：

- [ ] 所有函数有完整实现（无 TODO/pass/NotImplementedError）
- [ ] 所有函数有类型注解
- [ ] 对应的测试文件已创建且通过
- [ ] 节点函数遵循统一签名
- [ ] 错误处理已实现
- [ ] 审计日志已追加
- [ ] 与架构文档描述一致

---

## 5. 禁止事项清单

| 编号 | 禁止行为 | 原因 |
|------|---------|------|
| F-01 | 函数体为 `pass` / `...` / `raise NotImplementedError` | 模拟实现无意义 |
| F-02 | 返回硬编码假数据 | 无法验证真实逻辑 |
| F-03 | 测试中不写断言（只调用不验证） | 无法发现问题 |
| F-04 | 跳过错误处理 | 单节点异常会中止整个图 |
| F-05 | 不写审计日志 | 违反架构文档第14章要求 |
| F-06 | 修改 `AgentState` 字段定义 | 必须与架构文档6.1节一致 |
| F-07 | 在节点中直接实例化 LLM | 必须通过 `llm_client.py` 统一调用 |
| F-08 | 在代码中硬编码 API Key | 必须通过环境变量/配置文件 |
| F-09 | 创建架构文档中未定义的目录或文件 | 必须先更新架构文档再实现 |
| F-10 | 忽略测试覆盖率要求 | 每层有最低覆盖率标准 |

---

## 6. 参考文档

- 项目说明：[`README.md`](README.md)
- 子系统文档：[`docs/`](docs/)
- 本规范：[`DEVELOPMENT_GUIDELINES.md`](DEVELOPMENT_GUIDELINES.md)
- 私人文档（本地、不提交）：`docs-private/`
