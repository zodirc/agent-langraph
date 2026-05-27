# Confirmation gates (steer HITL)

> 版本：1.0 · 状态：已实现

结构化 **Intent / Outcome** 双门闸：规则在 `config.yaml` → `confirmation_gates`，执行在 `app/services/confirmation/`。

## 架构

| 模块 | 职责 |
|------|------|
| `gate_registry.py` | 声明式门闸求值（替代散落 frozenset） |
| `preview_resolver.py` | 按 work_item / intervention 选择 head/tail/range/diff/delta |
| `block_builder.py` | 统一 `ConfirmationBlock`（含 `sections[]`） |
| `snapshot.py` | 写作前 snapshot，Outcome diff |
| `writing_delta.py` | append 步增量节选 |
| `mission/steer_replan.py` | steer 后 work_plan patch + impact manifest |
| `mission/step_reconcile.py` | chapter_index 与 manuscript 对齐 |

## 配置

见 [`config/config.yaml`](../config/config.yaml) 中 `confirmation_gates`：

- `material_intervention_actions` — Intent 门闸触发
- `outcome_work_item_kinds` — Outcome 门闸触发
- `preview_defaults` — 各 kind 默认预览模式
- `cancel_on_intervention` — replan 时自动 cancel 的 pending kind

## API / UI

- 批准仍为 `POST /resume` 或 `/steer` 且 `{"confirm": true}`（见 `MANUSCRIPT_WRITING.md` §11.3）
- SSE / pause payload 中 `steer_*_confirmation.sections[]` 为结构化块
- Web CLI：`web/static/app.js` 按 `section.type` 渲染，不按 action 硬编码
- **去重**：确认面板仅在 SSE `done` 渲染一次（`mission_paused` 只发 system_lines）；`final_answer` 在门闸 pending 时不推送
- `/confirm` 与 `done` 共用 `renderSteerGateFromPayload`；Outcome 若本回合已有 `writing_delta` 则跳过重复 artifact 节选

## Planning 扩展

规划 LLM 可输出 `work_plan_patch`：

```json
{"cancel_ids": ["wi-12"], "prepend": [{"kind": "write_outline", "title": "重写大纲"}]}
```

`planning_node` 在 steer 回合调用 `apply_work_plan_patch`，Intent 门闸展示 queue + impact。

## 测试

- `tests/services/test_confirmation_gates.py`
- `tests/services/test_preview_resolver.py`
- `tests/services/test_steer_replan.py`
- 既有 `test_mission_steer_confirm.py` / `test_mission_steer_outcome_confirm.py`
