# Display pipeline & delivery policy

> 用户可见答案的空白保留、流式与终态一致性，以及按任务种类选择交付路径（配置驱动，非用户话术硬编码）。

---

## 1. 展示管线（`display`）

| 配置 | 作用 |
|------|------|
| `display.compose.preserve_code_whitespace` | `structured.artifacts[].content` 保留行首空格/Tab |
| `display.compose.preview_max_chars` | SSE `answer_preview` 上限（默认 8000） |
| `display.stream.include_artifact_code` | reasoning 流式解析 `artifacts[].content` |
| `display.stream.max_artifact_stream_chars` | 单轮 artifact 流式字符上限 |

实现：

- [`answer_compose.py`](../app/services/answer_compose.py) — 终态 `final_answer` 组装
- [`reasoning_trace.py`](../app/services/reasoning_trace.py) — `extract_artifact_code_content` + `emit_final_artifact_fences`
- [`reasoning_node.py`](../app/nodes/reasoning_node.py) — 推理结束后补齐 fenced code
- [`tool_node.py`](../app/nodes/tool_node.py) / [`artifact_content.py`](../app/services/artifact_content.py) — `artifact_profile=source_code` 时不 `strip()` 源码

---

## 2. 交付策略（`delivery`）

`config.yaml` → `delivery.by_kind` 定义每种推断任务如何交付：

```yaml
delivery:
  by_kind:
    code:
      primary: reasoning_artifacts      # structured.artifacts[]
      secondary: tool_write             # write_text_artifact via tool_execution
      default_extension: ".cpp"
      preserve_whitespace: true
      rewrite_manuscript_filenames: true
```

实现：[`delivery_policy.py`](../app/services/delivery_policy.py)

与 route audit 协作：

| 场景 | 行为 |
|------|------|
| `code` + `writing_manuscript` | 关闭 `writing_intent`，剥离 writing 工具，强制慢推理 |
| `code` + `writing_tools_only` | **保留** `write_text_artifact`，将 `novel.txt` 改写为 `{task_id}.cpp` |
| `artifact_profile` | 写入 `input_payload`，供 tool / artifact 网关使用 `source_code` 提示 |

---

## 3. 可观测性

规划后 pipeline 会输出两条 trace（`reasoning_trace_enabled` 时）：

1. **【路由审计】** — `report_route_audit_trace`
2. **【生效计划】** — `report_effective_plan_trace`（审计纠正后的工具/写作节点）

---

## 4. 记忆写回（`memory.writeback`）

[`memory_writeback_policy.py`](../app/services/memory_writeback_policy.py) 在 `write_turn_memories` 前过滤：

- `REJECTED` / `parser_fallback` 轮次不入库
- `code_verify_failed` 轮次不入库（见 `memory_writeback_policy`）

---

## 5. 代码校验与 LLM 修复

详见 **[`CODE_ARTIFACT_PIPELINE.md`](CODE_ARTIFACT_PIPELINE.md)**。

| 项 | 行为 |
|----|------|
| 裁判 | 以 `g++ -c` / `py_compile` 为主裁判；允许少量协议相关确定性修复作为预处理 |
| 修复 | 编译失败 → 先做最小确定性修复，再由 LLM 读取 `compiler_stderr` 修 `artifacts`；repair 提示 **不**硬编码缩进风格 |
| 展示空白 | `preserve_code_whitespace`：compose 时保留行首空格/Tab（与「修复」无关） |
| 流式 | 默认 `composed_at_end` |
| 失败 | 非低风险场景仍可 withhold + **REVIEW**；LOW 风险代码问答允许 `code_verify_degraded` 降级展示并附带告警，记忆写回继续跳过（§4） |

**已废弃 trace 字段：** `code_artifact_normalized`、`code_quality_reports`（旧版启发式管线，勿再依赖）。

---

## 6. 相关文档

- [`CODE_ARTIFACT_PIPELINE.md`](CODE_ARTIFACT_PIPELINE.md)
- [`ROUTE_AUDIT.md`](ROUTE_AUDIT.md)
- [`REASONING_SHORTCUT.md`](REASONING_SHORTCUT.md)
- [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md)
