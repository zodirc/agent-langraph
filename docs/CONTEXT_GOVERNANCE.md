# Context Governance — 实现说明

> 架构决议：[`ADR_CONTEXT_GOVERNANCE.md`](ADR_CONTEXT_GOVERNANCE.md)  
> **状态**：ADR §13 长期 DoD 已落地（代码 + 测试 + API）

## ADR §13 DoD 对照

| DoD | 状态 | 实现要点 |
|-----|------|----------|
| **13.1** 单一 `prompt_context_gateway` | ✅ | `prepare_governed_payload` / `build_context_envelope`；`llm_client` 在 `trace_state` 存在时自动治理 |
| **13.1** 无手拼上下文主路径 | ✅ | `planning_node` 经 `prepare_governed_user_json`；其余 JSON 调用传 `trace_state` |
| **13.1** `conversation_history` 仅 transcript 源 | ✅ | Prompt 侧由 envelope 提供；`transcript_for_llm_payload()` |
| **13.2** 五类上下文边界 | ✅ | `collect_context_items` + `context_registry` |
| **13.2** 各 `purpose` 显式策略 | ✅ | `context_policy.py`（含 `code_agent`、`reflection`） |
| **13.2** 远历史摘要/记忆 | ✅ | `semantic_summary` + episodic；`compress_session_history` |
| **13.3** token-aware + 字符 fail-safe | ✅ | `context_reducer` + `resolve_prompt_token_budget` 与 `BudgetContext` 统一 |
| **13.4** 可追踪 + 指标 + composition view | ✅ | `context_trace`；Prometheus 全套；`GET/POST` 任务 API |
| **§1.1 #6** Web 对话页可观测 + 手动压缩 | ✅ | `/chat` 内嵌「上下文治理」面板：`web/chat.html` + `web/static/context_governance_panel.js` |
| **13.5** 质量层 | ✅ | `tests/eval/test_context_governance_dod.py` 回归；`context_quality_regression_total` 指标 |

## 入口

| 组件 | 路径 | 职责 |
|------|------|------|
| 统一网关 | `app/services/prompt_context_gateway.py` | 组包、治理挂钩、手动压缩 |
| 注册表 | `app/services/context_registry.py` | retrieval/tool → `ContextItem` 持久化 |
| 文件切片 | `app/services/context_file_slice.py` | 按需局部读文件（§10.3） |
| LLM 挂钩 | `app/services/llm_client.py` | `trace_state` 自动 `apply_governance_to_user_content` |
| 组包 | `app/services/context_assembler.py` | 预算 → 裁剪 → messages |
| 策略 | `app/services/context_policy.py` | purpose 策略 + `resolve_purpose_for_state` |

## 配置

```yaml
context_governance:
  enabled: true
  default_token_budget: 64800
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/tasks/{id}/state` | 含 `context_composition`（最近组包） |
| GET | `/tasks/{id}/context-composition?purpose=writing` | 按需 composition view |
| POST | `/tasks/{id}/context/compress` | 受策略约束的手动压缩 `{scope, token_budget?}` |

`scope`: `transcript` | `all_compressible` | `aggressive`

## Web UI（对话页内嵌，非独立导航）

路径：`/chat` → 标题栏「对话」右侧 **上下文** 可折叠下拉面板。

| 能力 | 说明 |
|------|------|
| Purpose 预览 | 下拉切换 `planning` / `reasoning` / `writing` 等，调用 `GET …/context-composition` |
| 桶预算条 | 各 bucket `final_tokens / budget_tokens` |
| 保留 / 压缩 / 丢弃 | 可展开列表与 preview |
| 展开加载 | 点击「上下文」展开时拉取 composition；切换 Purpose 时刷新 |
| 手动压缩 | `POST …/context/compress`，scope + 可选 `token_budget`（默认 64800） |

实现：`web/static/context_governance_panel.js`；运行时桥接 `window.AgentChatRuntime`（`app.js`）。

## Prometheus（`METRICS_ENABLED=true`）

- `agent_context_bucket_tokens{purpose,bucket}`
- `agent_context_drop_total{purpose,bucket,reason}`
- `agent_context_compress_total{purpose,bucket,method}`
- `agent_context_recall_total{source}`
- `agent_context_overflow_prevented_total{purpose}`
- `agent_context_assembly_latency_ms{purpose}`
- `agent_context_quality_regression_total{purpose}`

## 测试

```bash
python3 -m pytest tests/services/test_context_governance.py tests/eval/test_context_governance_dod.py -q
python3 -m pytest tests/api/test_task_api.py::test_get_task_context_composition -q
```

## 豁免

`rag_eval` 等无 `AgentState` 的评测调用在 `GOVERNANCE_EXEMPT_PURPOSES` 中，不注入会话治理。
