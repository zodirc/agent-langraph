"""Unified replay harness for runtime regression (WP-3.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

REPLAY_ROOT = Path(__file__).resolve().parent
CASE_LIBRARY_DIRS = (
    REPLAY_ROOT / "golden",
    REPLAY_ROOT / "regression",
    REPLAY_ROOT / "boundary",
    REPLAY_ROOT / "performance",
)


@dataclass
class ReplayCase:
    name: str
    category: str
    state: dict[str, Any]
    assertions: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReplayResult:
    name: str
    passed: bool
    detail: str = ""


def discover_replay_cases(root: Path | None = None) -> list[Path]:
    base = root or REPLAY_ROOT
    paths: list[Path] = []
    for directory in CASE_LIBRARY_DIRS:
        if directory.exists():
            paths.extend(sorted(directory.glob("*.json")))
    if not paths and (base / "cases").exists():
        paths.extend(sorted((base / "cases").glob("*.json")))
    return paths


def run_replay_case(
    case: ReplayCase,
    runner: Callable[[dict[str, Any]], dict[str, Any]],
) -> ReplayResult:
    try:
        final = runner(case.state)
    except Exception as exc:  # pragma: no cover - harness reports failure
        return ReplayResult(case.name, False, str(exc))

    for key, expected in (case.assertions or {}).items():
        actual = final.get(key)
        if actual != expected:
            return ReplayResult(
                case.name,
                False,
                f"{key}: expected {expected!r}, got {actual!r}",
            )
    return ReplayResult(case.name, True)


def run_replay_suite(
    cases: Iterable[ReplayCase],
    runner: Callable[[dict[str, Any]], dict[str, Any]],
) -> list[ReplayResult]:
    return [run_replay_case(case, runner) for case in cases]


REPLAY_DIMENSIONS = (
    "routing",
    "interrupt",
    "tool_choice",
    "budget",
    "evidence",
    "ack_latency",
    "final_quality",
    "memory",
)


def assert_replay_dimensions(
    case_name: str,
    final_state: dict[str, Any],
    *,
    required: set[str],
    min_count: int = 3,
) -> None:
    """§5.5: each replay case covers at least min_count dimensions."""
    covered: set[str] = set()
    if final_state.get("current_node") or final_state.get("plan"):
        covered.add("routing")
    ctx = final_state.get("interrupt_context") or {}
    if final_state.get("event_type") in {"interrupt", "resume"} or ctx.get("runtime_state"):
        covered.add("interrupt")
    if final_state.get("selected_tools") or final_state.get("tool_results"):
        covered.add("tool_choice")
    if final_state.get("context_budget_buckets") or final_state.get("token_budget"):
        covered.add("budget")
    if final_state.get("retrieved_knowledge") or final_state.get("evidence_packets"):
        covered.add("evidence")
    if (final_state.get("foreground_status") or {}).get("last_ack"):
        covered.add("ack_latency")
    if final_state.get("final_answer"):
        covered.add("final_quality")
    if final_state.get("memory_hits"):
        covered.add("memory")
    assert len(covered & required) >= min(min_count, len(required)), f"{case_name}: {covered}"
