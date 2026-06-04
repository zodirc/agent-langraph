"""engineering_bounded execution path: plan → write → verify → repair → finalize."""

from __future__ import annotations

import json
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


def _generate_files_via_llm(
    state: AgentState,
    *,
    goal: str,
    intent_kind: str,
    repair_errors: str = "",
    prior_files: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    from app.services.llm_client import invoke_structured

    user = {
        "goal": goal,
        "intent_kind": intent_kind,
        "layout_hint": _default_layout(intent_kind, goal),
        "repair_errors": repair_errors or None,
        "prior_files": prior_files,
    }
    raw = invoke_structured(
        "reasoning",
        _ENGINEERING_SYSTEM,
        json.dumps(user, ensure_ascii=False),
        trace_state=state,
    )
    if not isinstance(raw, dict):
        return {"summary": "", "preview": "", "files": _default_layout(intent_kind, goal)}
    files = raw.get("files")
    if not isinstance(files, list) or not files:
        files = _default_layout(intent_kind, goal)
    cleaned: list[dict[str, str]] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path or ".." in path or path.startswith("/"):
            continue
        cleaned.append({"path": path, "content": str(item.get("content") or "")})
    if not cleaned:
        cleaned = _default_layout(intent_kind, goal)
    return {
        "summary": str(raw.get("summary") or ""),
        "preview": str(raw.get("preview") or ""),
        "files": cleaned,
    }


def _write_files(task_id: str, files: list[dict[str, str]]) -> list[str]:
    written: list[str] = []
    seen_dirs: set[str] = set()
    for item in files:
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        parent = str(Path(path).parent)
        if parent and parent not in (".", "") and parent not in seen_dirs:
            handle_mkdir_path(
                {"task_id": task_id, "path": parent, "parents": True, "exist_ok": True}
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
    return written


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
    if status in ("failed", "degraded") or degraded_reason:
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
    goal = str(payload.get("goal") or "")
    intent_kind = str(
        payload.get("intent_kind") or payload.get("route_audit", {}).get("inferred_kind") or "code"
    )
    task_id = str(state["task_id"])
    contract = get_mode_contract("engineering_mode")
    max_repairs = contract.execution.max_repair_attempts if contract else 3
    max_steps = contract.execution.max_steps if contract else 6
    budget = EngineeringStepBudget(max_steps=max_steps)

    backend_id = resolve_project_backend_id(intent_kind=intent_kind, goal=goal) or ""
    trace: dict[str, Any] = {
        "intent_kind": intent_kind,
        "target_mode": "engineering_mode",
        "execution_path": "engineering_bounded",
        "delivery_primary": "tool_write",
        "verify_backend": backend_id,
        "written_files": [],
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

    if not budget.consume("structure_plan"):
        degraded_reason = "步数预算耗尽（结构规划前）"
    else:
        plan = _generate_files_via_llm(state, goal=goal, intent_kind=intent_kind)
        summary = str(plan.get("summary") or "")

    if not budget.exhausted() and budget.consume("write_files"):
        written = _write_files(task_id, plan.get("files") or [])
        trace["written_files"] = written

    if not budget.exhausted() and written and budget.consume("read_back"):
        _read_key_files(task_id, written)

    verify = ProjectVerifyResult(
        ok=False,
        backend=backend_id,
        status="skipped",
        issues=["verify_skipped_budget"],
    )
    if not budget.exhausted() and budget.consume("verify"):
        verify = verify_project(task_id, intent_kind=intent_kind, goal=goal, backend_id=backend_id)
    trace["verify_result"] = verify.to_dict()

    repair_attempts = 0
    while (
        not verify.ok
        and repair_attempts < max_repairs
        and verify.status not in ("skipped", "degraded")
        and not budget.exhausted()
    ):
        repair_attempts += 1
        trace["repair_attempts"] = repair_attempts
        err_text = verify.stderr or "\n".join(verify.issues)
        if not err_text.strip():
            break

        if repair_attempts == 1 and budget.consume("repair_minimal"):
            if minimal_repair_files(
                task_id, written, stderr=err_text, intent_kind=intent_kind
            ):
                verify = verify_project(
                    task_id, intent_kind=intent_kind, goal=goal, backend_id=backend_id
                )
                trace["verify_result"] = verify.to_dict()
                if verify.ok:
                    break
            if budget.exhausted():
                break

        if not budget.consume("repair_llm"):
            degraded_reason = degraded_reason or "步数预算耗尽（LLM 修复前）"
            break

        prior = _read_key_files(task_id, written)
        plan = _generate_files_via_llm(
            state,
            goal=goal,
            intent_kind=intent_kind,
            repair_errors=err_text,
            prior_files=prior,
        )
        summary = str(plan.get("summary") or summary)
        if budget.consume("write_files"):
            written = _write_files(task_id, plan.get("files") or [])
            trace["written_files"] = written
        if budget.exhausted():
            break
        if budget.consume("verify"):
            verify = verify_project(
                task_id, intent_kind=intent_kind, goal=goal, backend_id=backend_id
            )
            trace["verify_result"] = verify.to_dict()

    if budget.exhausted() and not verify.ok:
        verify.status = "degraded"
        degraded_reason = degraded_reason or "engineering_bounded 步数预算已用尽"
    elif not verify.ok and verify.status != "skipped" and repair_attempts >= max_repairs:
        verify.status = "failed"

    if intent_kind == "interactive_app" and written:
        html = next((p for p in written if p.endswith("index.html")), written[0])
        preview = str(plan.get("preview") or "") or f"用浏览器打开 `{html}`（file:// 或静态服务器）。"
    elif intent_kind == "small_project":
        preview = str(plan.get("preview") or "") or "在工程目录执行 `make demo`。"
    else:
        preview = str(plan.get("preview") or preview)

    trace["steps_used"] = budget.used
    trace["steps_log"] = budget.steps_log

    answer = format_engineering_answer(
        summary=summary,
        written_files=written,
        preview=preview,
        verify_result=verify,
        degraded_reason=degraded_reason,
    )
    payload["engineering_trace"] = trace
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
