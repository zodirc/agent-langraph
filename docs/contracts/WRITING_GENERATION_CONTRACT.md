# Writing Generation Contract (WGC v1)

> 版本：`wgc/1` · 实现：`app/services/writing_contract.py`、`artifact_args_parser.py`、`writing_generation.py`、`llm_gateway._stream_artifact_live`

## 1. 目的

长文写作通过 `submit_artifact` 工具流式返回正文。WGC 规定：

- 模型输出形态（tool args / 字段）
- 流式解析与 UI (`writing_delta`) 的语义
- 断流、超时、超长 args 时的行为

避免「缓冲很大但手稿区空白」与「502 后整段丢失」无据可查。

## 2. 契约字段

| 项 | 要求 |
|----|------|
| `delivery` | 默认 `tool`：`submit_artifact` |
| `tool.args` | 仅允许 `content` 键（禁止 `reasoning` / `thinking` 等前缀字段） |
| `content` | 中文正文或大纲纯文本，非 meta 评论 |
| `partial_commit_policy` | 默认 `allow_on_disconnect`：transport 中断且已解析出 partial content 时可提交 |

配置：`config.yaml` → `performance.writing_generation`。

## 3. 流式管线

```text
LLM stream chunk
  → tool_call_chunks.args 片段 → ArtifactArgsParser.feed
  → extract_streaming_content → emit_writing_content_deltas (SSE)
  → 结束：adapt_raw_response(merged) 或 partial fallback
  → WritingGenerationRecord 写入 draft.meta / audit
```

### 3.1 解析

- 主路径：`ArtifactArgsParser.content_so_far()`（支持未闭合 JSON 字符串）
- 回退：`extract_field_text(accumulated, "content")`（正则）

### 3.2 限制（可配置）

| 配置键 | 默认 | 行为 |
|--------|------|------|
| `max_stream_duration_sec` | 600 | 超时停止读流，尝试 partial |
| `max_accumulated_chars` | 120000 | args 超限熔断 |
| `stream_max_retries` | 2 | transport 可重试次数 |
| `buffer_stall_trace_chars` | 8000 | 无 content 时 trace 附诊断前缀 |
| `partial_commit_on_disconnect` | true | 502/断流保留已解析正文 |

## 4. 生成生命周期

每次流式调用创建 `WritingGenerationRecord`（`generation_id`），结束时写入 `draft.meta`：

- `generation_status`: `streaming` → `committed` | `partial` | `aborted` | `failed`
- `args_bytes`, `content_bytes_parsed`, `stream_duration_ms`
- `time_to_first_content_ms`, `error_class`, `stream_close`

写作节点 audit 可通过 `draft.meta` 追溯单次生成。

## 5. 指标（contract_events）

| 事件 | 含义 |
|------|------|
| `writing_content_first_byte` | 首次向 UI 推送正文 |
| `writing_stream_stall` | 缓冲超阈值仍无 content |
| `writing_stream_transport_error` | 断流/502 等 |
| `writing_stream_duration_cap` | 达到时长上限 |
| `writing_stream_args_cap` | 达到 args 累积上限 |

## 6. 运维

- 流式 502：查 `claudecode-internal` / nginx `proxy_read_timeout`，应大于 `max_stream_duration_sec`。
- dev 环境：避免长写作中 WatchFiles 热重载（会中断进程内 HTTP 流）。
- 排障：`/audit <task_id>` 查看 `draft.meta.generation_*` 与 `stream_interrupted`。

## 7. 路线图

| 阶段 | 状态 |
|------|------|
| Phase 0 | partial 落盘、transport 重试、limits、stall 诊断 |
| Phase 1 | WGC 文档、增量解析、generation 元数据 |
| Phase 2 | 段级 DB 持久化 generation、仪表板 |
| Phase 3 | 动态分段、模型能力路由、replay CI |

参见 [`MANUSCRIPT_WRITING.md`](../MANUSCRIPT_WRITING.md) § 流式写作与 WGC。
