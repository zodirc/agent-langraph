# Agent LangGraph Runtime

基于 LangGraph 的通用多领域 Agent Runtime（v0.10+），实现见 [`DEVELOPMENT_GUIDELINES.md`](DEVELOPMENT_GUIDELINES.md) 与 [`docs/`](docs/) 子系统说明。

## quick start（3 步）

需要 **Docker**。可同时配置多个厂商 Key，用 `MODEL_PROVIDER` 指定当前使用哪一个：

```bash
cp .env.example .env                   # 填写各厂商 Key；设置 MODEL_PROVIDER=anthropic 等
make init && make up                   # HTTPS + 代码热更新
# 切换厂商：改 MODEL_PROVIDER / MODEL_NAME 后 make restart
```

浏览器打开 **https://localhost:8080/** 平台首页；对话页 **https://localhost:8080/chat**（虚拟机局域网：`https://<VM-IP>:8080/`，setup 会打印地址）。

- 默认 **关闭认证**（`AUTH_ENABLED=false`），可直接用；要开启见 `.env` 设 `AUTH_ENABLED=true` 后 `make restart`。
- 高级环境变量：[`docs/ENV_REFERENCE.md`](docs/ENV_REFERENCE.md)
- 等价命令：`./scripts/setup.sh && docker compose up -d --build`（`COMPOSE_FILE` 由 init 写入）

### 完整开发环境

## 入口（§8）

| 入口 | 说明 |
|------|------|
| **平台首页** | https://localhost:8080/ |
| **对话 (Web CLI)** | https://localhost:8080/chat（`HOST_PORT`，本地默认 `make up` 即 HTTPS + 热更新） |
| **Skills 目录** | https://localhost:8080/skills |
| **监控面板** | https://localhost:8080/dashboard |
| **独立 CLI** | `python -m app.cli --local run "你的任务"` |
| **HTTP API** | 见下方 API 列表 |
| **Scheduler** | `POST /schedules`（Cron） |

## Web CLI

> UI 现代化方案（本地 Docker + 宿主机 Vite 开发）：[`docs/WEB_UI_MODERNIZATION.md`](docs/WEB_UI_MODERNIZATION.md)

- 直接输入任务目标（SSE 流式）
- `/history` `/status <id>` `/result <id>`
- `/approve <id>` `/reject <id>`
- `/risk high <text>`
- `/supervisor 分析文档并审查代码`

### 近期前端更新（2026-06-03）

基于今天的 commits，Web 端最近一轮更新主要集中在 **Skills 能力入口**、**平台 UI 统一化**、**对话页交互增强**、以及 **文件/产物操作安全性** 四块：

- **Skills 能力平台化**：新增 Skills 浏览、管理、测试入口，形成从目录浏览、草稿编辑、发布、版本回滚到 dry-run 的闭环。
- **平台导航与认证统一**：平台首页、聊天页、dashboard、skills 系列页面共享统一导航与平台样式，前端认证接入也更完整。
- **Chat 交互增强**：聊天页持续补强终端样式、任务流展示、交互反馈与可视化细节，提升 Web CLI 的可操作性。
- **产物与任务清理补强**：配合后端最近的 artifact 删除、任务清理与审计写入能力，前端在任务/产物管理上的交互基础更完整。

相关实现可参考 [`web/static/app.js`](web/static/app.js)、[`web/static/platform.css`](web/static/platform.css)、[`web/static/terminal.css`](web/static/terminal.css)、[`web/static/skills.js`](web/static/skills.js)、[`app/api/skills_api.py`](app/api/skills_api.py:1)、[`app/services/task_cleanup.py`](app/services/task_cleanup.py:1)。

## 独立 CLI（§8.2）

```bash
# 本地进程内执行（无需启动 uvicorn）
python -m app.cli --local run "解释 LangGraph 架构"
python -m app.cli --local status <task_id>
python -m app.cli --local result <task_id>
python -m app.cli --local run "多领域任务" --supervisor

# 远程 HTTP API
python -m app.cli run "任务目标"   # 默认 http://127.0.0.1:8000
```

## 认证（可选）

默认 `auth.enabled=false`。

```bash
export AUTH_ENABLED=true
export ADMIN_PASSWORD=your-password
export SERVICE_API_KEY=your-service-key
```

## 知识库（§23）

- `POST /knowledge/documents` — SQLite + 向量索引（Chroma / Qdrant）
- `GET /knowledge/search?q=...&mode=hybrid|vector|keyword`
- 配置 `knowledge.backend`: `chroma` | `qdrant`
- 配置 `knowledge.embedding_model`: `voyage-3`（需 `VOYAGE_API_KEY`）、`local_minilm`（容器内本地模型）或 `default`（本地确定性向量兜底）

## 核心 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 平台首页 |
| GET | `/chat` | Web CLI 对话页 |
| GET | `/cli` | 302 → `/chat`（兼容旧链接） |
| GET | `/dashboard` | 任务监控面板 |
| GET | `/health` | 健康检查 |
| POST | `/tasks` | 创建任务（可选 `skill_id` + `skill_params`） |
| POST | `/tasks/stream` | SSE 流式 |
| GET | `/skills` | 技能目录 |
| GET | `/skills/{id}` | 技能详情 |
| POST | `/skills` | 创建自定义技能草稿 |
| PUT | `/skills/{id}` | 更新草稿 |
| POST | `/skills/{id}/publish` | 发布 |
| POST | `/skills/{id}/clone` | 克隆（含官方模板） |
| GET | `/skills/{id}/versions` | 版本历史 |
| POST | `/skills/{id}/versions/{v}/rollback` | 回滚 |
| POST | `/skills/{id}/dry-run` | 预览 policy / 工具 / planning prompt |
| GET | `/metrics/skills` | 按 skill 聚合指标 |
| GET | `/skills/test` (Web) | Skill 测试台 |
| GET | `/skills/marketplace/packages` | 已安装 skill 包 |
| PUT | `/skills/admin/global-disabled` | 全局禁用 skill（admin） |
| PUT | `/skills/admin/tenants/{id}/blocks` | 租户屏蔽 skill（admin） |
| GET | `/skills` (Web) | https://localhost:8080/skills 浏览与选用 |
| GET | `/skills/manage` (Web) | https://localhost:8080/skills/manage 自定义技能管理 |
| GET | `/tasks/{id}/status` | 状态 + **node_history** 时间线 |
| GET | `/tasks/{id}/audit` | 审计链路 |
| POST | `/supervisor/tasks` | Supervisor 任务 |
| POST | `/supervisor/tasks/stream` | Supervisor SSE |
| GET | `/dead-letter` | 死信队列列表 |
| POST | `/dead-letter/{id}/requeue` | 重新入队执行 |
| GET | `/metrics/summary` | 指标摘要（多租户时含 `tenant` 块） |
| GET | `/metrics/tenant` | 当前租户配额 + LLM 成本估算 |
| GET | `/metrics` | Prometheus 格式 |
| POST | `/tenants` | 创建租户存储（admin，`tenant.enabled`） |
| DELETE | `/tenants/{id}` | 删除租户存储 |
| GET | `/tenants/{id}/quota` | 租户配额快照 |
| POST | `/batches` | 批量任务 |
| POST | `/schedules` | 定时调度 |
| GET | `/domains` | 领域能力包 |
| GET | `/domains/prompt-catalog` | Prompt 模板库目录 |
| POST | `/feedback` | 任务反馈（学习闭环） |
| GET | `/a2a/agent-card` | A2A Agent Card |
| POST | `/a2a/messages` | A2A 能力派发 |
| GET | `/tasks/{id}/artifacts` | 任务产出文件列表 |
| GET | `/tasks/{id}/artifacts/{filename}` | 下载文本 artifact |

长文手稿（小说/报告续写）设计见 [`docs/MANUSCRIPT_WRITING.md`](docs/MANUSCRIPT_WRITING.md)（Manuscript 状态 + Writing 节点）。  
规划后路径审计（代码 vs 手稿误路由）见 [`docs/ROUTE_AUDIT.md`](docs/ROUTE_AUDIT.md)。展示缩进保留与代码交付策略见 [`docs/DISPLAY_AND_DELIVERY.md`](docs/DISPLAY_AND_DELIVERY.md)；**编译校验**、规划 JSON 修复、确定性最小代码修复、stderr 驱动 LLM 修复与低风险降级策略见 [`docs/CODE_ARTIFACT_PIPELINE.md`](docs/CODE_ARTIFACT_PIPELINE.md)。

## Agent 框架对照（Appendix C）

本书对比多种 Agent 框架；**本仓库选用 LangGraph** 作为运行时核心。

| 维度 | **本仓库 (LangGraph)** | CrewAI | Google ADK |
|------|------------------------|--------|------------|
| 核心抽象 | 显式 `StateGraph` + 条件边 | Role / Task / Crew 角色协作 | Agent + Tool + Session 服务化 |
| 状态与恢复 | `AgentState` + SQLite/Postgres checkpoint | 任务级上下文，较弱显式图状态 | Session/Artifact 托管 |
| 人工审核 | `interrupt_before` + `/reviews` | 需自定义 HITL | 依赖上层产品 |
| 工具/MCP | 统一 `ToolRegistry` + MCP bridge | 工具封装在 Agent | 工具与代码执行集成 |
| 多 Agent | Supervisor 图 + Worker 子图 + A2A 消息 | 原生 Crew 编排 | 多 Agent 路由/委托 |
| 可观测 | SSE trace + Prometheus + LangSmith | 有限内置 | Cloud Trace 生态 |
| 部署形态 | 自托管 FastAPI / Docker | Python 库为主 | 偏 GCP/Vertex 托管 |

运行时图与节点编排见 [`app/runtime/graph.py`](app/runtime/graph.py)、[`app/services/graph_runner.py`](app/services/graph_runner.py)。

## 子系统文档

| 文档 | 内容 |
|------|------|
| [`docs/ROUTE_AUDIT.md`](docs/ROUTE_AUDIT.md) | 规划后任务类型 vs 执行路径审计、reflection 重规划 |
| [`docs/REASONING_SHORTCUT.md`](docs/REASONING_SHORTCUT.md) | 多轮推理隔离：每轮清零、问答强制完整思考 |
| [`docs/CONFIRMATION_GATES.md`](docs/CONFIRMATION_GATES.md) | Intent / Outcome 双阶段确认门闸、预览策略、gate registry |
| [`docs/MISSION_EXECUTION_CONTROL.md`](docs/MISSION_EXECUTION_CONTROL.md) | execution grant、pause_reason、resume / steer 控制面 |
| [`docs/SESSION_TURN_POLICY.md`](docs/SESSION_TURN_POLICY.md) | session turn 规划闸门、机械续写、mission active 场景决策 |
| [`docs/RETRIEVAL_OPTIMIZATION.md`](docs/RETRIEVAL_OPTIMIZATION.md) | 知识检索优化：BM25、CJK token、候选扩展、rerank 默认开启 |
| [`docs/CODE_ARTIFACT_PIPELINE.md`](docs/CODE_ARTIFACT_PIPELINE.md) | 编译校验、stderr 驱动 LLM 修复、composed_at_end 流式 |
| [`docs/DISPLAY_AND_DELIVERY.md`](docs/DISPLAY_AND_DELIVERY.md) | 代码缩进保留、流式 artifacts、delivery 与记忆写回门控 |
| [`docs/WEB_UI_MODERNIZATION.md`](docs/WEB_UI_MODERNIZATION.md) | Web 对话界面现代化（React/Vite、Docker 构建、分阶段实施） |
| [`app/config/prompt_templates.py`](app/config/prompt_templates.py) | 按 `purpose` / `domain` / `reasoning_mode` 选择 Prompt |
| `config.yaml` → `reasoning_trace` · [`app/services/reasoning_trace.py`](app/services/reasoning_trace.py) | SSE trace、`thinking` 字段 |
| `GET /domains/prompt-catalog` | HTTP 查询模板目录 |

## 会话模式（Copilot 式，一会话一 Task）

默认开启：`session.enabled: true`，**同一 `session_id` = 同一 `task_id`**。

| 行为 | 说明 |
|------|------|
| Web CLI | 自动 `localStorage` 保存 `session_id`；同窗口多轮输入续聊 |
| `/new` | 新开会话（新 `session_id` + 新 task） |
| `/session` | 查看当前 session id |
| 存储 | `conversation_history` 由 [`conversation_context.py`](app/services/conversation_context.py) 统一写入 state + `input_payload`（user/assistant 成对） |
| 压缩 | 超出 `max_history_turns` / `max_history_chars` 自动裁剪；可选 `context_compress.semantic_enabled` 语义摘要 |
| 记忆 | 多轮时 `session.memory_retrieval_enabled`（默认 true）在 planning 设 `skip_retrieval` 时仍检索 session memory |
| 审核中断 | `WAITING_REVIEW` 前也会把 reasoning 草稿写入 history（`persist_turn_draft_answer`） |
| 代码展示 | reasoning 将代码放入 `structured.artifacts`；`answer_compose` 组装最终 Markdown |
| 输出治理 | `output_guard` 仅对 **prose** 段做 PII 扫描，代码块内长数字不误杀 |
| 路径审计 | [`route_audit`](docs/ROUTE_AUDIT.md)：规划后比对任务类型与执行路径，误入手稿写作时自动纠正 |
| 多轮推理隔离 | [`REASONING_SHORTCUT.md`](docs/REASONING_SHORTCUT.md)：每轮清零；问答不走「只报文件大小」 |
| API | `POST /tasks/stream` 传 `session_id`、`new_session`；`GET /tasks/{id}/conversation` 查历史 |

```json
{
  "session_id": "uuid-from-client",
  "new_session": false,
  "input_payload": { "goal": "续写上一章" }
}
```

## Mission Steer 与确认（长篇）

| API | 用途 |
|------|------|
| `POST /tasks/{id}/steer` | 运行中：排队；已暂停：立即应用。body 可含 `message`、`intervention`、`confirm` |
| `POST /tasks/{id}/resume` | 继续下一工作项；`{"confirm": true}` 批准 intent/outcome 门闸 |
| Web `/confirm` | 等价 `resume` + `confirm:true` |

- 运行中输入 → **steer 队列**；刷新后同 session 提交 → **session turn**（须先 planning，见文档）。
- 批准门闸 **仅** 结构化 `confirm:true`，不用自然语言「确认」匹配。
- 双阶段：规划理解（intent）→ 执行 → 产物节选（outcome，如大纲写完）。

详见 [`docs/MANUSCRIPT_WRITING.md`](docs/MANUSCRIPT_WRITING.md) §11。

## 内置 Agent Prompt（服务端）

规划 / 推理节点自动注入 `app/config/prompts.py` 中的 **AGENT_CORE_PROMPT**（身份、工具能力、长篇分批写文件策略）。**不会**在 Web CLI 或 API 响应中返回给用户。

可选在 `config.yaml` → `agent.prompt_extra` 追加团队规范（仍仅服务端生效）。

## 内置工具（P0）

规划节点只会选用已注册工具；本地无联网搜索时，请用知识库检索 + 下列工具：

| 工具 | 用途 |
|------|------|
| `get_runtime_info` | 回答「你是什么模型 / 能否联网」等，返回真实 `MODEL_NAME` 与能力说明 |
| `calculator` | 大整数 / 精确算术（勿心算） |
| `write_text_artifact` | 将文本写入任务目录下的文件 |
| `append_text_artifact` | 向已有文件追加内容（长文分块写入） |
| `edit_text_artifact` | 对已有文本 artifact 做精确替换，常用于 outline / plot 局部修订 |
| `read_text_artifact` | 读取已写入的文件 |
| `echo` / `summarize_text` | 调试与摘要 |

配置项（`config.yaml` → `artifacts`）：`base_path`、`max_write_bytes`、`max_file_bytes`。

## Agent 与硬编码边界

- **核心路径**：用户目标 → **规划 LLM**（可选工具列表）→ 可选检索/工具 → **推理 LLM** 生成回答。
- **工具**：注册能力，由规划模型按需选用；不应靠正则把特定问法绑到固定工具或固定回复。
- **可固化**：配置中的真实上限（`get_runtime_info` / `runtime_limits`）、文件安全校验、重试策略等**事实**，不是问法匹配。
- **可选捷径**（默认关闭）：`fast_reasoning` 对确定性工具跳过推理 LLM（`get_runtime_info` 已始终走推理 LLM）。
- **已移除**：`fast_planning`（规则规划）；长文/续写由规划 LLM 决定是否用 `write/append` 工具。
- **Supervisor**：子任务 domain 由分解 LLM 根据 worker catalog 选择，无关键词路由。

## 性能优化（可选捷径）

| 机制 | 说明 |
|------|------|
| `performance.fast_reasoning_enabled` | 确定性工具结果模板回复（默认 `false`） |
| `performance.skip_retrieval_when_no_tools` | 无工具且无 retrieve 步骤时跳过向量检索 |
| `model.max_tokens_by_purpose` | 规划 4096 / 推理 8192 / 写作 16384 等上限 |

检索是否执行由规划 LLM 的 `skip_retrieval` 决定，Web CLI **不再**用正则推断 `needs_search`。

SSE 额外事件：`tool_preview`（工具结果片段）、`answer_preview`（推理摘要提前展示）。

调优示例（`config/config.yaml`）：

```yaml
performance:
  fast_reasoning_enabled: false
  skip_retrieval_when_no_tools: true
model:
  timeout: 45
  max_retries: 2
  max_tokens_by_purpose:
    planning: 4096
    reasoning: 8192
  timeout: 120
knowledge:
  top_k: 3
```

`GET /health` 返回 `fast_reasoning`、`model_max_tokens_*` 便于确认配置已加载。

## 架构能力对照（100% 文档项）

### 阶段 1–5（MVP）
- 8 核心节点 + Policy 拒绝 + **Dead Letter** 节点
- LangGraph 执行图 + SQLite checkpoint 隔离
- Supervisor-Worker + 领域包 + 并行 Worker

### §20 多 Agent
- **Worker LangGraph 子图**（`app/runtime/worker_graph.py`）
- Supervisor 级 Worker **失败重试**（`supervisor.worker_retries`）

### §22 错误恢复
- 工具/推理节点 **重试环** → 超限进入 **DLQ**
- `GET/POST /dead-letter/*` 查询与重新入队
- 人工审核 **SLA 超时** 自动拒绝/升级（`policy.review_timeout_*`）

### §14–15 可观测与 API
- **node_history** 状态流转时间线
- Prometheus `/metrics` + 面板 `/dashboard`
- LangSmith 可选（`observability.langsmith_enabled`）

### §21–23 生产演进
- **Qdrant** 向量后端（`knowledge.backend: qdrant`）
- **Voyage-3** Embedding
- **Celery + Redis** 批量队列（`queue.backend: celery`）

## 配置示例

```yaml
# config/config.yaml
knowledge:
  backend: chroma          # chroma | qdrant
  embedding_model: voyage-3

supervisor:
  max_workers: 4
  worker_retries: 2

policy:
  max_retry_count: 3
  review_timeout_minutes: 60
  review_timeout_action: reject

queue:
  backend: memory          # memory | celery
  redis_url: redis://localhost:6379/0

observability:
  metrics_enabled: true
  langsmith_enabled: false
```

## 本地 Docker 部署（推荐）

**双容器**：`agent`（应用）+ `postgres`（内置数据库），数据卷持久化，符合架构文档 §25。

- **PostgreSQL**：任务状态、审计、死信、记忆、LangGraph checkpoint（避免 SQLite 锁导致 SSE 断连）
- **agent_data 卷**：Chroma 向量库、artifact 文件、日志

### 1. 准备环境变量

```bash
cd agent-langraph   # 你的克隆目录
cp .env.example .env
# 编辑 .env：MODEL_PROVIDER + 对应 API Key（见 .env.example；无 Key 时走本地规则推理）
```

### 2. 一键启动

```bash
docker compose up -d --build
```

> 若你已同步本次“增强记忆 / 长输出 / 吞吐优化”实现，Docker 环境建议重建镜像后再启动，确保容器中的[`config/config.docker.yaml`](config/config.docker.yaml)与代码一致生效。

### 3. 访问

| 地址 | 说明 |
|------|------|
| https://localhost:8080/ | 平台首页 |
| https://localhost:8080/chat | Web CLI 对话（提交任务；`HOST_PORT` 可改） |
| https://localhost:8080/dashboard | 监控面板 |
| https://localhost:8080/health | 健康检查 |
| https://localhost:8080/docs | OpenAPI 文档 |

**宿主机访问虚拟机**（示例 IP `192.168.25.128`）：

| 用途 | 地址 |
|------|------|
| 浏览器 / 平台 | `https://192.168.25.128:8080/` |
| 对话 | `https://192.168.25.128:8080/chat` |
| 宿主机 `curl` 测通（明文） | `http://192.168.25.128:8081/health` |

- 浏览器访问 HTTPS 时接受自签证书警告；Web CLI 内 `/login admin <密码>`。
- **Windows 自带 `curl` + HTTPS + IP** 常报 `SEC_E_INTERNAL_ERROR`（Schannel 限制），属客户端问题，不是服务未启动。请任选：
  - 用浏览器打开 `https://192.168.25.128:8080/`；
  - 或用明文测通：`curl http://192.168.25.128:8081/health`；
  - 或在 `C:\Windows\System32\drivers\etc\hosts` 增加 `192.168.25.128 agent.local` 后访问 `https://agent.local:8080/`；
  - 或安装 [curl for Windows](https://curl.se/windows/)（OpenSSL 版），勿用 Schannel。
- 虚拟机放通端口：`sudo firewall-cmd --add-port=8080/tcp --add-port=8081/tcp --permanent && sudo firewall-cmd --reload`
- 浏览器若仍异常，先在虚拟机执行：`docker compose up -d --force-recreate caddy`（须禁用 HTTP/3，见 `deploy/caddy/Caddyfile`）
- `.env` 示例：`PUBLIC_DOMAIN=localhost, 192.168.25.128` 与 `DEFAULT_SNI=192.168.25.128`（**逗号后空格**）

### 4. 常用命令

```bash
docker compose logs -f agent       # 应用日志
docker compose logs -f postgres   # 数据库日志
docker compose ps                # 状态（应看到 agent + postgres 均 healthy）
docker compose down              # 停止
docker compose down -v           # 停止并删除卷（清空 PG + 向量库）
```

### 5. 不用 compose 时

```bash
docker build -t agent-langraph .
docker run -d --name agent-langraph -p 8000:8000 \
  -v agent_data:/data \
  -e CONFIG_PATH=/app/config/config.docker.yaml \
  -e ANTHROPIC_API_KEY=sk-ant-xxx \
  --env-file .env \
  agent-langraph
```

数据保存在 Docker 卷：`agent_pg_data`（PostgreSQL）、`agent_data`（向量库/artifact/日志）。

### 6. Docker 下本次优化的关键配置

当前 Docker 配置已经同步以下优化项：

- [`config/config.docker.yaml`](config/config.docker.yaml) → `artifacts.max_write_bytes: 1048576`
- [`config/config.docker.yaml`](config/config.docker.yaml) → `artifacts.max_file_bytes: 20971520`
- [`config/config.docker.yaml`](config/config.docker.yaml) → `artifacts.chunk_chars: 6000`
- [`config/config.docker.yaml`](config/config.docker.yaml) → `artifacts.max_chunks_per_turn: 8`
- [`config/config.docker.yaml`](config/config.docker.yaml) → `artifacts.max_chars_per_turn: 32000`
- [`config/config.docker.yaml`](config/config.docker.yaml) → `performance.llm_streaming_enabled: true`
- [`config/config.docker.yaml`](config/config.docker.yaml) → `performance.result_cache_ttl_seconds: 300`
- [`config/config.docker.yaml`](config/config.docker.yaml) → `performance.prefetch_retrieval: true`

这意味着 Docker 本地部署环境也会启用：
- 更大的长输出分块与文件上限
- 推理流式输出能力
- 轻量结果缓存
- 检索预热开关

### 7. 代码热更新（默认 HTTPS）

本地默认 **`make up`**（或叠加 `docker-compose.dev.yml`）：**HTTPS 经 Caddy** + **uvicorn `--reload`**，访问 **https://localhost:8080/**（平台）与 **/chat**（对话，端口见 `.env` `HOST_PORT`）。

```bash
make up          # 启动（HTTPS + 热更新）
make logs        # 看 agent 日志（含 reload）
make restart     # 改 config/ 后重启 agent
make down        # 停止
```

| 改动 | 行为 |
|------|------|
| `app/` | uvicorn 自动 reload |
| `web/static/`、`web/*.html` | 浏览器强刷 |
| `config/` | `make restart` |
| `requirements.txt` / `Dockerfile` | `make build` |

无热更新、代码在镜像内（接近线上）：

```bash
make prod        # 仅 docker-compose.yml，HTTPS，无 reload
```

| 命令 | HTTPS | 热更新 |
|------|-------|--------|
| `make up` | 是（Caddy） | 是 |
| `make prod` | 是（Caddy） | 否 |

### 8. 内置 PostgreSQL 说明

`docker-compose.yml` 已默认启动 `postgres` 服务，应用通过 Docker 网络连接：

```text
postgresql://agent:agent@postgres:5432/agent
```

启动后验证：

```bash
curl -sk https://localhost:8080/health | python3 -m json.tool
# storage_backend: postgres
# checkpoint_backend: postgres
```

凭据可在 `.env` 中修改 `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB`（与 compose 中 postgres 服务一致）。

表结构由应用启动时自动创建（`init_postgres_schema` + `PostgresSaver.setup()`），无需手工建库。

若需回退 SQLite（仅本地调试）：在 compose 中注释掉 `postgres` 服务，并设置 `STORAGE_BACKEND=sqlite`。

### 9. Docker 下的配置与多租户 / 鉴权

容器内**不要**改 `config/config.yaml` 路径假设；Compose 通过环境变量固定：

```text
CONFIG_PATH=/app/config/config.docker.yaml
APP_ENV=production          # 生产 compose
DATABASE_URL=postgresql://agent:agent@postgres:5432/agent
```

| 场景 | 改哪个文件 / 命令 |
|------|-------------------|
| 生产 Docker 部署 | 宿主机 `.env` + [`config/config.docker.yaml`](config/config.docker.yaml) |
| 本地非 Docker | [`config/config.yaml`](config/config.yaml) |
| 开发热更新 | 挂载 `./config`，改 **`config.docker.yaml`**（`docker-compose.dev.yml`） |

生产默认 **`AUTH_ENABLED=true`**（`config.docker.yaml` + compose）。若需关闭认证，请叠加开发 compose（`APP_ENV=development`），勿在生产环境关闭。

多租户 + 分布式配额（多副本）：

```bash
# .env: MULTI_TENANT_ENABLED=true, APP_SECRET_KEY=...
docker compose -f docker-compose.yml -f docker-compose.redis.yml up -d --build
```

登录后请求需带 JWT 与一致的 `X-Tenant-Id`（见 [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md) §4.1）。

```bash
curl -ks -X POST https://localhost/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"'"$ADMIN_PASSWORD"'"}'
# 使用返回的 access_token + X-Tenant-Id
```

## 测试

```bash
pytest tests/ -v    # 60 tests
```
