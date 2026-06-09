"""Progress summary for interactive steer timeouts (optimization.md §3.9)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.runtime.state import AgentState


def _parse_iso(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_steer_progress_summary(state: AgentState, *, elapsed_sec: float | None = None) -> str:
    payload = state.get("input_payload") or {}
    ms = state.get("manuscript") or {}
    observation = state.get("observation") or {}
    step = int(state.get("mission_step") or 0)
    started = payload.get("steer_turn_started_at") or payload.get("steer_applied_at")
    if elapsed_sec is None and started:
        started_dt = _parse_iso(str(started))
        if started_dt is not None:
            elapsed_sec = (datetime.now(timezone.utc) - started_dt).total_seconds()

    parts: list[str] = []
    if elapsed_sec is not None:
        parts.append(f"本纠偏已运行约 {int(elapsed_sec)} 秒")
    parts.append(f"mission 步数 {step}")
    outline_b = int(ms.get("outline_bytes") or 0)
    body_b = int(ms.get("body_bytes") or 0)
    if outline_b:
        parts.append(f"大纲 {outline_b} 字节")
    if body_b:
        parts.append(f"正文 {body_b} 字节")

    delta = observation.get("artifact_delta") or {}
    if isinstance(delta, dict) and delta.get("has_change"):
        ob = int(delta.get("outline_bytes_delta") or 0)
        bb = int(delta.get("body_bytes_delta") or 0)
        if ob or bb:
            parts.append(f"本轮变更：大纲 {ob:+d} B，正文 {bb:+d} B")

    contract = payload.get("turn_contract") or {}
    primary = str(contract.get("primary_op") or "")
    if primary:
        parts.append(f"当前计划：{primary}")

    planning_calls = int(payload.get("steer_planning_llm_calls") or 0)
    if planning_calls:
        parts.append(f"规划 LLM 已调用 {planning_calls} 次")

    return "；".join(parts) + "。如需继续，请补充更具体的修改说明或发送「继续」。"
