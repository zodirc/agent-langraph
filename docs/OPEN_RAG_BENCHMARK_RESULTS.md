# Open RAG 评测结果（Baseline）

> 快照时间：2026-06-04（UTC）  
> 环境：Docker agent 容器，`config.docker.yaml`，`embedding_model: local_minilm`（`all-MiniLM-L6-v2`），`rerank_backend: lexical`，`top_k: 5`  
> 全量 JSON 报告（含 `per_task`）在本地 `tests/eval/reports/baselines/`（**gitignore**，可用 `./scripts/snapshot_open_rag_baselines.sh` 刷新）

## 1. 汇总

| 评测集 | 任务数 | 语料 | recall@5 | MRR | NDCG@5 | context_redundancy | 说明 |
|--------|--------|------|----------|-----|--------|-------------------|------|
| **QReCC closed-corpus** | 1000 | 1200 docs | **0.862** | **0.642** | **0.697** | 0.014 | 通用对话检索回归（Rewrite query） |
| QReCC 子集（调试） | 10 | 见注 | 0.700 | 0.362 | 0.445 | 0.000 | 早期子集，仅供参考 |
| **Writing 规范库** | 8 | 4 docs | **1.000** | **0.813** | — | 0.350 | `knowledge/writing/*.md`，非长篇正文 |
| QReCC sample | 2 | 3 docs | 1.000 | 1.000 | 1.000 | 0.000 | smoke |
| SciFact sample | 2 | 3 docs | 1.000 | 1.000 | 1.000 | 0.000 | smoke |

注：Writing 集 NDCG 在多金标任务上可能大于 1.0（聚合方式导致），仅作集内对比，不与 QReCC 横比绝对值。

## 2. QReCC 1000（主 baseline）

- **数据**：`tests/eval/open_rag/normalized/qrecc_test_{corpus,rewrite_tasks}.jsonl`
- **设定**：test split 前 1000 query；语料 = 1000 金标 Answer + 200 干扰；closed-corpus（非 open-domain 5400 万 passage）
- **未命中率**：约 **13.8%**（138/1000，recall@5=0）
- **主要失败模式**：同一会话内检索到其它 turn 的 Answer，金标 turn 未进 top-5
- **域**：`common`

### 2.1 命令

```bash
docker compose exec agent python tests/eval/open_rag/scripts/convert_qrecc.py --split test --max-queries 1000
docker compose exec agent python tests/eval/run_open_rag_eval.py \
  --benchmark qrecc \
  --corpus tests/eval/open_rag/normalized/qrecc_test_corpus.jsonl \
  --tasks tests/eval/open_rag/normalized/qrecc_test_rewrite_tasks.jsonl \
  --output tests/eval/reports/qrecc_test_1000_eval.json
```

## 3. Writing 规范库（8 条）

- **数据**：`tests/eval/open_rag/writing_tasks.yaml` → `convert_writing_knowledge.py`
- **语料**：`builtin-writing-guidelines`、`builtin-prose-voice-format` + common 干扰
- **域**：`writing`；部分任务启用 `enrich_writing`（与线上一致）
- **边界**：验证规范 md 检索与域过滤，**不**代表两三千字章节 chunk 检索

### 3.1 命令

```bash
docker compose exec agent python tests/eval/open_rag/scripts/convert_writing_knowledge.py
docker compose exec agent python tests/eval/run_open_rag_eval.py --benchmark writing
```

## 4. 结论与后续

| 层次 | 结论 |
|------|------|
| 通用 hybrid + local_minilm | QReCC 1000 上 recall@5=0.86，可作为调参前后 **检索底座** baseline |
| 写作规范 RAG | 8 条样本上表现良好；长篇正文需 **artifact/chunk 金标** 另测 |
| CI | 常规守门仍为 `tests/eval/test_rag_golden.py`；open RAG 为 **手动 benchmark** |

## 5. 本地快照与对比

```bash
./scripts/snapshot_open_rag_baselines.sh
# 生成 tests/eval/reports/baselines/comparison.json（gitignore）
```

改 RAG 配置后重跑评测，对比本文件中的数值是否退化。
