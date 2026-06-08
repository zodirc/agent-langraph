# Agent LangGraph Runtime

## 1. 启动要求（快速部署）

### 环境要求

- Linux / macOS / Windows（建议使用 Docker 环境）
- Docker
- Docker Compose
- 可选的模型服务密钥

### 快速启动

```bash
cp .env.example .env
make init && make up
```

### 启动后访问

- 平台首页：`https://localhost:8080/`
- 对话页面：`https://localhost:8080/chat`
- 健康检查：`https://localhost:8080/health`

### 说明

- 默认通过 Docker 启动完整运行环境
- 如需切换模型供应商，修改 `.env` 中相关配置后重启
- 如需重新加载配置，执行重启即可

---

## 2. 文档阅读参考

建议按以下顺序阅读：

1. [`docs/arch.md`](docs/arch.md)
   - 当前项目完整架构与流程总览
   - 包含系统分层、主执行链路、执行模式与 ASCII 流程图

2. [`docs/rag_skills.md`](docs/rag_skills.md)
   - RAG、代码检索、Tools、MCP、Skill、上下文与记忆的详细说明
   - 适合理解项目中几个最核心也最容易混淆的概念
