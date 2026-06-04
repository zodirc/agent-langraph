# 会话目录文件工具

> 实现：`app/services/session_fs_tools.py` · 注册：`app/services/builtin_tools.py`  
> 路径根：`task_artifact_dir(task_id)`（与 Web 会话文件侧栏、`PUT .../files/content` 同源）

Agent 对文件的读写**限定在当前 task 的会话根目录**，不能访问宿主机任意路径，也不能执行任意 shell。

---

## 1. 工具列表

| 工具 | 作用 | 风险 |
|------|------|------|
| `mkdir_path` | 创建目录 | LOW |
| `touch_file` | 创建空文件 | LOW |
| `write_file` | 写入（覆盖）文本 | LOW |
| `append_file` | 追加文本 | LOW |
| `read_file` | 按 offset/max_chars 读取 | LOW |
| `ls_path` | 列目录（可 recursive，有 max_entries） | LOW |
| `grep_file` | 文件内按 pattern 搜行 | LOW |
| `replace_in_file` | 精确/正则替换（可 dry_run） | LOW |
| `move_path` | 移动/重命名 | LOW |
| `copy_path` | 复制文件或目录 | LOW |
| `rm_path` | 删除（**两阶段**：先 dry_run 得 token，再 confirm） | LOW |
| `verify_backend` | 白名单工程/编译校验（见 [`CODE_AND_ENGINEERING_PATHS.md`](CODE_AND_ENGINEERING_PATHS.md)） | LOW |

手稿 / mission 另有 **`read_text_artifact` / `edit_text_artifact` / `write_text_artifact`** 等，作用在 manuscript 绑定路径，与上表「会话沙箱」不同。

---

## 2. 路径规则

- 参数 `path` / `src` / `dst` 必须为**相对路径**
- 解析后 `resolve()` 必须仍在会话根下（`is_relative_to(root)`）
- 禁止 `..`、禁止绝对路径
- 部分操作禁止直接针对会话根 `.`（防误删整树）

与工程节点内部写文件使用同一套 `_resolve_in_session`（`engineering_execution._write_files`）。

---

## 3. 谁能在何时调用

| 运行时 | 文件工具如何生效 |
|--------|------------------|
| **`engineering_mode`** | 主路径：**不依赖** planner 选 `write_file`；`engineering_execution` 在节点内调用 `handle_write_file` / `handle_mkdir_path`。契约 `allowed_tools` 含 `mkdir_path, write_file, read_file, verify_backend`（供显式 tool 计划或后续扩展）。 |
| **`qa_mode`** | 契约 `allowed_tools: []`；默认不走会话写文件工具（代码展示走 `code_artifact` 临时目录）。 |
| **`manuscript_mode`** | 契约禁止工程工具集；写作走 `writing_node` / mission artifact 工具。 |
| **Planning 选中工具** | `tool_execution_node` 执行 registry 中工具；受 `tool_intent_guard` 与模式契约过滤。 |
| **Mission worker** | `WorkerExecutionPolicy.allowed_tools` 可含 `read_text_artifact`、`grep_file` 等（见 ADR OMAW）。 |
| **Web / API** | `GET/PUT /tasks/{id}/files/...` 人工编辑；与 agent 工具共享目录。 |

---

## 4. 与工程校验的关系

工程模式落盘后，`verify_backend` 或节点内 `verify_project()` 会：

1. 将会话目录树复制到 `project_verify.workspace_root` 下的隔离 workspace
2. 按 intent 选择 `cpp` / `python` / `web_html_js` / `make_cpp_demo` 执行白名单命令

详见 [`CODE_AND_ENGINEERING_PATHS.md`](CODE_AND_ENGINEERING_PATHS.md)。

---

## 5. 测试与文档索引

- 安全：`tests/services/test_project_verify_security.py`
- 工程写盘：`tests/services/test_engineering_execution.py`、`tests/integration/test_engineering_mode_flow.py`
- 实现状态：[`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md) 验收表「Session 文件工具集」
