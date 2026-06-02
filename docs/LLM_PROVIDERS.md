# LLM 厂商配置

模型通过 **`.env` + `config/config.yaml` 的 `model` 段** 选定，无需改代码。

## 环境变量（见 `.env.example`）

| 变量 | 说明 |
|------|------|
| `MODEL_PROVIDER` | `anthropic` \| `openai` \| `deepseek` \| `glm` \| `openai_compat` |
| `MODEL_NAME` | 模型 ID；留空则用该厂商默认值 |
| `MODEL_ENABLED` | `true` / `false` |
| `MODEL_BASE_URL` | 可选，覆盖默认 API 地址 |
| `MODEL_API_KEY` | 可选通用密钥（`openai_compat` 或 yaml 回退） |

各厂商专用密钥（优先于 `MODEL_API_KEY`）：

| Provider | 环境变量 | 默认 base_url |
|----------|----------|----------------|
| anthropic | `ANTHROPIC_API_KEY` | `https://api.anthropic.com` |
| openai | `OPENAI_API_KEY` | `https://api.openai.com/v1` |
| deepseek | `DEEPSEEK_API_KEY` | `https://api.deepseek.com` |
| glm | `ZHIPUAI_API_KEY` / `GLM_API_KEY` | `https://open.bigmodel.cn/api/paas/v4/` |

## 示例

**DeepSeek：**

```bash
MODEL_PROVIDER=deepseek
MODEL_NAME=deepseek-v4-flash
DEEPSEEK_API_KEY=sk-...
```

**智谱 GLM：**

```bash
MODEL_PROVIDER=glm
MODEL_NAME=glm-4.7
ZHIPUAI_API_KEY=...
```

**自建 OpenAI 兼容代理：**

```bash
MODEL_PROVIDER=openai_compat
MODEL_BASE_URL=https://your-proxy/v1
MODEL_API_KEY=sk-...
MODEL_NAME=your-model-id
```

## 实现位置

- 注册表：`app/llm/registry.py`
- 工厂：`app/llm/factory.py`
- 业务入口（不变）：`app/services/llm_client.get_llm` / `invoke_structured`

探针：`python3 scripts/probe_model_output.py`（Anthropic 原生 HTTP 探针在 `anthropic` 厂商下最准确）。
