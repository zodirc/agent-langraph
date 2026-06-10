"""engineering_bounded execution path: plan → write → verify → repair → finalize."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.engineering_repair import minimal_repair_files
from app.services.mode_registry import get_mode_contract
from app.services.project_verify.backends import (
    ProjectVerifyResult,
    resolve_project_backend_id,
    slug_from_goal,
    verify_project,
)
from app.services.session_fs_tools import handle_mkdir_path, handle_read_file, handle_write_file

logger = logging.getLogger(__name__)


def _engineering_control_checkpoint(task_id: str, phase: str) -> None:
    """Honor pause/cancel between engineering_bounded steps."""
    from app.services.execution_control import check_for_control_signal

    check_for_control_signal(
        str(task_id),
        phase=phase,
        raise_on_pause=True,
        raise_on_cancel=True,
    )

_NON_RETRYABLE_VERIFY_ISSUES = frozenset(
    {
        "no_backend_for_intent",
        "backend_not_whitelisted",
        "verify_disabled",
        "session_root_missing",
        "no_files_written",
    }
)

_GOAL_CODE_RE = re.compile(
    r"(?i)(c\+\+|cpp|\.cpp|\.cc|python|\.py|py_compile|g\+\+|可编译|源码|单文件)"
)
_GOAL_WEB_RE = re.compile(
    r"(?i)(网页|html|javascript|js\b|游戏|2048|前端|浏览器|vue|react)"
)
_GOAL_MAKE_RE = re.compile(r"(?i)(makefile|make\s+demo|cmake|small_project)")

_ENGINEERING_SYSTEM = """You are an engineering agent. Return ONE JSON object only:
{
  "summary": "brief outcome in user language",
  "preview": "how to open or run the deliverable",
  "files": [{"path": "relative/path", "content": "full file text"}]
}
Rules:
- paths must be relative (no .., no absolute paths)
- interactive_app: use games/<slug>/index.html, game.js, style.css
- small_project: use projects/<slug>/ with Makefile target demo and src/
- code (C++/Python): minimal files under src/ or session root
- Do not include markdown fences in file content.
"""


@dataclass
class EngineeringStepBudget:
    max_steps: int
    used: int = 0
    steps_log: list[str] = field(default_factory=list)

    def consume(self, name: str, cost: int = 1) -> bool:
        if self.used + cost > self.max_steps:
            return False
        self.used += cost
        self.steps_log.append(name)
        return True

    def exhausted(self) -> bool:
        return self.used >= self.max_steps


@dataclass
class WriteFilesResult:
    written: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _default_layout(intent_kind: str, goal: str) -> list[dict[str, str]]:
    kind = (intent_kind or "code").lower()
    if kind == "interactive_app":
        base = slug_from_goal(goal, prefix="games")
        return [
            {"path": f"{base}/index.html", "content": ""},
            {"path": f"{base}/game.js", "content": ""},
            {"path": f"{base}/style.css", "content": ""},
        ]
    if kind == "small_project":
        base = slug_from_goal(goal, prefix="projects")
        return [
            {"path": f"{base}/Makefile", "content": "demo:\n\t@echo demo\n"},
            {"path": f"{base}/src/main.cpp", "content": ""},
        ]
    ext = ".py" if re.search(r"(?i)python|\.py", goal) else ".cpp"
    return [{"path": f"main{ext}", "content": ""}]


def _unwrap_plan_result(raw: Any) -> tuple[dict[str, Any], str | None]:
    """Accept (plan, err) tuple or legacy dict-only mocks in tests."""
    if isinstance(raw, tuple) and len(raw) >= 2:
        plan = raw[0] if isinstance(raw[0], dict) else {}
        err = raw[1] if len(raw) > 1 else None
        return plan, str(err) if err else None
    if isinstance(raw, dict):
        return raw, None
    return {"summary": "", "preview": "", "files": []}, "invalid_plan_shape"


def _normalize_planned_files(
    raw: Any,
    *,
    intent_kind: str,
    goal: str,
) -> list[dict[str, str]]:
    if not isinstance(raw, list) or not raw:
        return _default_layout(intent_kind, goal)
    cleaned: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path or ".." in path or path.startswith("/"):
            continue
        cleaned.append({"path": path, "content": str(item.get("content") or "")})
    return cleaned or _default_layout(intent_kind, goal)


def _generate_files_via_llm(
    state: AgentState,
    *,
    goal: str,
    intent_kind: str,
    repair_errors: str = "",
    prior_files: list[dict[str, str]] | None = None,
) -> tuple[dict[str, Any], str | None]:
    """Return (plan dict, error_message or None)."""
    from app.services.llm_client import invoke_structured

    user = {
        "goal": goal,
        "intent_kind": intent_kind,
        "layout_hint": _default_layout(intent_kind, goal),
        "repair_errors": repair_errors or None,
        "prior_files": prior_files,
    }
    try:
        raw = invoke_structured(
            "reasoning",
            _ENGINEERING_SYSTEM,
            json.dumps(user, ensure_ascii=False),
            trace_state=state,
        )
    except Exception as exc:
        logger.warning("engineering plan LLM failed: %s", exc)
        files = _default_layout(intent_kind, goal)
        return (
            {
                "summary": "",
                "preview": "",
                "files": files,
                "plan_source": "fallback_layout",
            },
            str(exc),
        )

    if not isinstance(raw, dict):
        files = _default_layout(intent_kind, goal)
        return (
            {"summary": "", "preview": "", "files": files, "plan_source": "fallback_layout"},
            "planner_returned_non_object",
        )

    files = _normalize_planned_files(raw.get("files"), intent_kind=intent_kind, goal=goal)
    return (
        {
            "summary": str(raw.get("summary") or ""),
            "preview": str(raw.get("preview") or ""),
            "files": files,
            "plan_source": "llm",
        },
        None,
    )


def _write_files(task_id: str, files: list[dict[str, str]]) -> WriteFilesResult:
    written: list[str] = []
    errors: list[str] = []
    seen_dirs: set[str] = set()
    for item in files:
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        try:
            parent = str(Path(path).parent)
            if parent and parent not in (".", "") and parent not in seen_dirs:
                handle_mkdir_path(
                    {
                        "task_id": task_id,
                        "path": parent,
                        "parents": True,
                        "exist_ok": True,
                    }
                )
                seen_dirs.add(parent)
            handle_write_file(
                {
                    "task_id": task_id,
                    "path": path,
                    "content": str(item.get("content") or ""),
                    "parents": True,
                }
            )
            written.append(path)
        except (ValueError, OSError, FileNotFoundError) as exc:
            errors.append(f"{path}: {exc}")
            logger.warning("engineering write failed %s: %s", path, exc)
    return WriteFilesResult(written=written, errors=errors)


def _plan_file_dicts(plan: dict[str, Any]) -> list[dict[str, str]]:
    raw = plan.get("files")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, dict) and item.get("path"):
            out.append(
                {"path": str(item["path"]), "content": str(item.get("content") or "")}
            )
    return out


def _refine_intent_kind(
    intent_kind: str,
    goal: str,
    files: list[dict[str, str]],
) -> str:
    """Map general/qa intents to code/small_project/interactive_app from goal + planned files."""
    kind = (intent_kind or "general").strip().lower()
    if kind in ("interactive_app", "small_project", "code"):
        return kind

    text = (goal or "").strip()
    if _GOAL_WEB_RE.search(text):
        return "interactive_app"
    if _GOAL_MAKE_RE.search(text):
        return "small_project"
    if _GOAL_CODE_RE.search(text):
        return "code"

    paths = [str(f.get("path") or "") for f in files]
    lower_paths = " ".join(paths).lower()
    if any(p.endswith(".html") for p in paths) or any(p.endswith(".js") for p in paths):
        return "interactive_app"
    if "makefile" in lower_paths:
        return "small_project"
    if any(p.endswith((".cpp", ".cc", ".py")) for p in paths):
        return "code"

    bid = resolve_project_backend_id(intent_kind=kind, goal=goal, files=files)
    if bid in ("cpp", "python"):
        return "code"
    if bid == "web_html_js":
        return "interactive_app"
    if bid == "make_cpp_demo":
        return "small_project"
    return kind


def _resolve_verify_backend(
    *,
    intent_kind: str,
    goal: str,
    written: list[str],
    plan_files: list[dict[str, str]],
) -> str:
    files = plan_files or [{"path": p, "content": ""} for p in written]
    return resolve_project_backend_id(intent_kind=intent_kind, goal=goal, files=files) or ""


def _sync_delivery_targets(
    *,
    intent_kind: str,
    goal: str,
    written: list[str],
    plan_files: list[dict[str, str]],
    trace: dict[str, Any],
) -> tuple[str, str]:
    files = plan_files or [{"path": p, "content": ""} for p in written]
    intent_kind = _refine_intent_kind(intent_kind, goal, files)
    backend_id = _resolve_verify_backend(
        intent_kind=intent_kind,
        goal=goal,
        written=written,
        plan_files=files,
    )
    trace["intent_kind"] = intent_kind
    trace["verify_backend"] = backend_id
    return intent_kind, backend_id


def _verify_is_non_retryable(verify: ProjectVerifyResult) -> bool:
    return any(str(i) in _NON_RETRYABLE_VERIFY_ISSUES for i in (verify.issues or []))


def _degraded_reason_for_verify(
    verify: ProjectVerifyResult,
    *,
    intent_kind: str,
    backend_id: str,
    budget_exhausted: bool,
    prior: str,
    write_errors: list[str] | None = None,
) -> str:
    parts: list[str] = []
    if prior:
        parts.append(prior)
    if write_errors:
        parts.append("部分文件落盘失败: " + "; ".join(write_errors[:4]))

    issues = [str(i) for i in (verify.issues or [])]
    if "no_files_written" in issues:
        parts.append("没有文件成功落盘，已跳过 project_verify。")
    elif "no_backend_for_intent" in issues:
        parts.append(
            "未匹配 project_verify 后端（intent_kind="
            f"{intent_kind!r}, backend={backend_id!r}）。"
            "请在 goal 中写明 C++/Python/网页/Makefile，或落盘对应扩展名；"
            "单文件 C++ 使用 cpp 后端（g++ -c），不是 make demo。"
        )
    elif "verify_concurrency_limit" in issues:
        parts.append("校验并发已满，请稍后重试。")
    elif "verify_skipped_budget" in issues:
        parts.append("步数预算不足，未完成 project_verify 校验。")

    if budget_exhausted and not verify.ok:
        parts.append("engineering_bounded 步数预算已用尽（结构/落盘/校验/修复步数用完）")

    stderr = (verify.stderr or "").strip()
    if stderr and not verify.ok:
        parts.append("校验输出: " + stderr[:500])

    if verify.ok and verify.status == "ok":
        return " ".join(parts) if parts else ""
    if not parts:
        return "校验未通过，已停止受控修复。请根据 trace 中的 stderr 调整后重试。"
    return " ".join(parts)


def _run_verify(
    task_id: str,
    *,
    intent_kind: str,
    goal: str,
    backend_id: str,
    written: list[str],
) -> ProjectVerifyResult:
    if not written:
        return ProjectVerifyResult(
            ok=False,
            backend=backend_id,
            status="skipped",
            issues=["no_files_written"],
        )
    if not backend_id:
        return ProjectVerifyResult(
            ok=False,
            backend="",
            status="failed",
            issues=["no_backend_for_intent"],
        )
    try:
        return verify_project(
            task_id,
            intent_kind=intent_kind,
            goal=goal,
            backend_id=backend_id,
        )
    except Exception as exc:
        logger.exception("verify_project failed for %s", task_id)
        return ProjectVerifyResult(
            ok=False,
            backend=backend_id,
            status="degraded",
            issues=["verify_exception"],
            stderr=str(exc),
        )


def _read_key_files(task_id: str, paths: list[str], *, limit: int = 3) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for path in paths[:limit]:
        try:
            result = handle_read_file(
                {"task_id": task_id, "path": path, "offset": 0, "max_chars": 12000}
            )
            out.append({"path": path, "content": str(result.get("content") or "")})
        except (FileNotFoundError, ValueError, OSError):
            continue
    return out


def _preview_for_delivery(
    *,
    intent_kind: str,
    written: list[str],
    plan: dict[str, Any],
    backend_id: str,
    preview: str,
) -> str:
    if str(plan.get("preview") or "").strip():
        return str(plan["preview"])
    if intent_kind == "interactive_app" and written:
        html = next((p for p in written if p.endswith("index.html")), written[0])
        return f"用浏览器打开 `{html}`（file:// 或静态服务器）。"
    if intent_kind == "small_project":
        return "在含 Makefile 的工程目录执行 `make demo`。"
    if backend_id == "cpp" and written:
        cpp = next((p for p in written if p.endswith((".cpp", ".cc"))), written[0])
        return f"编译示例：`g++ -c {cpp}`（会话沙箱内由 project_verify 执行）。"
    if backend_id == "python" and written:
        py = next((p for p in written if p.endswith(".py")), written[0])
        return f"语法检查：`python3 -m py_compile {py}`。"
    return preview or "在会话 artifact 目录中打开上述文件。"


def format_engineering_answer(
    *,
    summary: str,
    written_files: list[str],
    preview: str,
    verify_result: ProjectVerifyResult | dict[str, Any],
    degraded_reason: str = "",
) -> str:
    vr = verify_result if isinstance(verify_result, dict) else verify_result.to_dict()
    status = str(vr.get("status") or ("ok" if vr.get("ok") else "failed"))
    backend = str(vr.get("backend") or "")
    issues = vr.get("issues") or []
    stderr = str(vr.get("stderr") or "").strip()
    lines = [
        "## 摘要",
        summary or "工程交付已完成。",
        "",
        "## 文件清单",
    ]
    if written_files:
        for p in written_files:
            lines.append(f"- `{p}`")
    else:
        lines.append("- （无落盘文件）")
    lines.extend(["", "## 打开/预览方式", preview or "在会话 artifact 目录中打开上述文件。"])
    lines.extend(["", "## 校验结果", f"- backend: `{backend}`", f"- status: **{status}**"])
    if issues:
        lines.append(f"- issues: {', '.join(str(i) for i in issues[:6])}")
    if stderr and status != "ok":
        lines.append(f"- stderr: {stderr[:800]}")
    show_degraded = status in ("failed", "degraded") or (
        bool(degraded_reason) and status != "ok"
    )
    if show_degraded:
        lines.extend(
            [
                "",
                "## 降级说明",
                degraded_reason
                or "校验未通过，已停止受控修复。请根据 trace 中的 stderr 调整后重试。",
            ]
        )
    return "\n".join(lines)


def run_engineering_bounded(state: AgentState) -> AgentState:
    """Bounded engineering loop; sets final_answer and engineering_trace on payload."""
    payload = dict(state.get("input_payload") or {})
    goal = str(payload.get("goal") or "").strip()
    intent_kind = str(
        payload.get("intent_kind")
        or (payload.get("route_audit") or {}).get("inferred_kind")
        or "code"
    )
    task_id = str(state["task_id"])
    _engineering_control_checkpoint(task_id, "engineering_entry")
    contract = get_mode_contract("engineering_mode")
    max_repairs = max(0, int(contract.execution.max_repair_attempts if contract else 3))
    max_steps = max(3, int(contract.execution.max_steps if contract else 6))
    budget = EngineeringStepBudget(max_steps=max_steps)

    plan_files: list[dict[str, str]] = []
    backend_id = ""
    write_errors: list[str] = []
    trace: dict[str, Any] = {
        "intent_kind": intent_kind,
        "target_mode": "engineering_mode",
        "execution_path": "engineering_bounded",
        "delivery_primary": "tool_write",
        "verify_backend": backend_id,
        "written_files": [],
        "write_errors": [],
        "verify_result": {},
        "repair_attempts": 0,
        "max_steps": max_steps,
        "steps_used": 0,
        "steps_log": [],
    }
    degraded_reason = ""
    summary = ""
    preview = ""
    written: list[str] = []
    plan: dict[str, Any] = {}
    repair_attempts = 0
    verify = ProjectVerifyResult(
        ok=False,
        backend="",
        status="skipped",
        issues=["not_started"],
    )

    if not goal:
        degraded_reason = "工程交付需要非空的 goal（描述要生成的项目或文件）。"
        verify = ProjectVerifyResult(
            ok=False,
            backend="",
            status="skipped",
            issues=["empty_goal"],
        )
    elif not budget.consume("structure_plan"):
        degraded_reason = "步数预算耗尽（结构规划前）"
        verify = ProjectVerifyResult(
            ok=False,
            backend="",
            status="skipped",
            issues=["verify_skipped_budget"],
        )
    else:
        _engineering_control_checkpoint(task_id, "before_structure_plan")
        plan, plan_err = _unwrap_plan_result(
            _generate_files_via_llm(state, goal=goal, intent_kind=intent_kind)
        )
        _engineering_control_checkpoint(task_id, "after_structure_plan")
        if plan_err:
            trace["plan_error"] = plan_err
            degraded_reason = degraded_reason or f"结构规划异常，已使用默认骨架：{plan_err}"
        summary = str(plan.get("summary") or "")
        plan_files = _plan_file_dicts(plan)

        if not budget.exhausted() and budget.consume("write_files"):
            _engineering_control_checkpoint(task_id, "before_write_files")
            wf = _write_files(task_id, plan.get("files") or [])
            written = wf.written
            write_errors = wf.errors
            trace["written_files"] = written
            trace["write_errors"] = write_errors
            if write_errors and not written:
                degraded_reason = degraded_reason or "全部文件落盘失败"
            elif write_errors:
                degraded_reason = degraded_reason or "部分文件落盘失败"

            intent_kind, backend_id = _sync_delivery_targets(
                intent_kind=intent_kind,
                goal=goal,
                written=written,
                plan_files=plan_files,
                trace=trace,
            )
            _engineering_control_checkpoint(task_id, "after_write_files")

        if not budget.exhausted() and written and budget.consume("read_back"):
            _engineering_control_checkpoint(task_id, "before_read_back")
            _read_key_files(task_id, written)
            _engineering_control_checkpoint(task_id, "after_read_back")

        verify = ProjectVerifyResult(
            ok=False,
            backend=backend_id,
            status="skipped",
            issues=["verify_skipped_budget"],
        )
        if not budget.exhausted() and budget.consume("verify"):
            _engineering_control_checkpoint(task_id, "before_verify")
            verify = _run_verify(
                task_id,
                intent_kind=intent_kind,
                goal=goal,
                backend_id=backend_id,
                written=written,
            )
            _engineering_control_checkpoint(task_id, "after_verify")
        trace["verify_result"] = verify.to_dict()

        repair_attempts = 0
        while (
            not verify.ok
            and repair_attempts < max_repairs
            and verify.status not in ("skipped", "degraded")
            and not _verify_is_non_retryable(verify)
            and not budget.exhausted()
        ):
            _engineering_control_checkpoint(task_id, "repair_loop")
            repair_attempts += 1
            trace["repair_attempts"] = repair_attempts
            err_text = (verify.stderr or "").strip() or "\n".join(verify.issues)
            if not err_text.strip():
                break

            if repair_attempts == 1 and budget.consume("repair_minimal"):
                _engineering_control_checkpoint(task_id, "before_repair_minimal")
                if minimal_repair_files(
                    task_id, written, stderr=err_text, intent_kind=intent_kind
                ):
                    verify = _run_verify(
                        task_id,
                        intent_kind=intent_kind,
                        goal=goal,
                        backend_id=backend_id,
                        written=written,
                    )
                    trace["verify_result"] = verify.to_dict()
                    if verify.ok:
                        break
                _engineering_control_checkpoint(task_id, "after_repair_minimal")
                if budget.exhausted():
                    break

            if not budget.consume("repair_llm"):
                degraded_reason = degraded_reason or "步数预算耗尽（LLM 修复前）"
                break

            _engineering_control_checkpoint(task_id, "before_repair_llm")
            plan, plan_err = _unwrap_plan_result(
                _generate_files_via_llm(
                    state,
                    goal=goal,
                    intent_kind=intent_kind,
                    repair_errors=err_text,
                    prior_files=_read_key_files(task_id, written),
                )
            )
            _engineering_control_checkpoint(task_id, "after_repair_llm")
            if plan_err:
                trace.setdefault("repair_plan_errors", []).append(plan_err)
            summary = str(plan.get("summary") or summary)
            plan_files = _plan_file_dicts(plan)

            if budget.consume("write_files"):
                _engineering_control_checkpoint(task_id, "before_repair_write_files")
                wf = _write_files(task_id, plan.get("files") or [])
                written = wf.written or written
                write_errors = list(write_errors) + wf.errors
                trace["written_files"] = written
                trace["write_errors"] = write_errors
                intent_kind, backend_id = _sync_delivery_targets(
                    intent_kind=intent_kind,
                    goal=goal,
                    written=written,
                    plan_files=plan_files,
                    trace=trace,
                )
                _engineering_control_checkpoint(task_id, "after_repair_write_files")
            if budget.exhausted():
                break
            if budget.consume("verify"):
                _engineering_control_checkpoint(task_id, "before_repair_verify")
                verify = _run_verify(
                    task_id,
                    intent_kind=intent_kind,
                    goal=goal,
                    backend_id=backend_id,
                    written=written,
                )
                _engineering_control_checkpoint(task_id, "after_repair_verify")
                trace["verify_result"] = verify.to_dict()

    if not verify.ok and _verify_is_non_retryable(verify):
        verify.status = "degraded"
    elif budget.exhausted() and not verify.ok:
        verify.status = "degraded"
    elif not verify.ok and verify.status != "skipped" and repair_attempts >= max_repairs:
        verify.status = "failed"

    degraded_reason = _degraded_reason_for_verify(
        verify,
        intent_kind=intent_kind,
        backend_id=backend_id,
        budget_exhausted=budget.exhausted(),
        prior=degraded_reason,
        write_errors=write_errors,
    )

    preview = _preview_for_delivery(
        intent_kind=intent_kind,
        written=written,
        plan=plan,
        backend_id=backend_id,
        preview=preview,
    )

    trace["steps_used"] = budget.used
    trace["steps_log"] = budget.steps_log
    trace["verify_result"] = verify.to_dict()

    answer = format_engineering_answer(
        summary=summary,
        written_files=written,
        preview=preview,
        verify_result=verify,
        degraded_reason=degraded_reason,
    )
    payload["engineering_trace"] = trace
    payload["intent_kind"] = intent_kind
    payload["engineering_delivery"] = {
        "written_files": written,
        "verify_backend": backend_id,
        "verify_status": verify.status,
    }

    return merge_state(
        state,
        input_payload=payload,
        final_answer=answer,
        reasoning_result={
            "summary": answer,
            "structured": {"engineering": trace},
            "confidence": 0.85 if verify.ok else 0.4,
        },
        status=TaskStatus.REASONED.value,
        current_node="engineering_execution",
        skip_retrieval=True,
        audit_log=append_audit(
            state,
            "engineering_execution",
            verify.status,
            trace,
        ),
    )
