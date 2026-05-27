"""Golden-task evaluation baseline (Ch19)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.config.prompts import build_reasoning_system_prompt
from app.runtime.router import route_after_policy_to_guard
from app.runtime.state import create_initial_state, merge_state
from app.services.output_guard import evaluate_output
from app.services.resource_budget import BudgetContext, BudgetExceededError
from tests.eval.eval_metrics import record


def _load_golden_tasks() -> list[dict]:
    path = Path(__file__).parent / "golden_tasks.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(data.get("tasks") or [])


@pytest.mark.parametrize("task", _load_golden_tasks(), ids=lambda t: t["id"])
def test_golden_task(task: dict) -> None:
    kind = task.get("kind", "unit")
    if kind != "unit":
        pytest.skip("only unit golden tasks in baseline")

    task_id = task["id"]
    if task_id.startswith("policy_"):
        state = merge_state(
            create_initial_state(),
            policy_result=task["policy_result"],
        )
        ok = route_after_policy_to_guard(state) == task["expect_route"]
        assert ok
        record(task_id, passed=1.0 if ok else 0.0)

    elif task_id.startswith("output_guard_"):
        result = evaluate_output(task["text"], llm_review=False)
        ok = result.passed is task["expect_passed"]
        assert ok
        record(task_id, passed=1.0 if ok else 0.0)

    elif task_id == "budget_exceeded":
        ctx = BudgetContext(token_limit=int(task["token_limit"]))
        with pytest.raises(BudgetExceededError):
            ctx.before_invoke("test", "x" * int(task["prompt_size"]), "")
        record(task_id, passed=1.0)

    elif task_id == "reasoning_mode_cot":
        prompt = build_reasoning_system_prompt(task["reasoning_mode"])
        ok = task["expect_substring"] in prompt.lower()
        assert ok
        record(task_id, passed=1.0 if ok else 0.0)

    elif task_id == "judge_completed_task":
        from tests.eval.llm_judge import judge_task_state

        scores = judge_task_state(task["task_state"], goal="explain")
        overall = float(scores["overall"])
        assert overall >= float(task["expect_min_overall"])
        record(task_id, overall=overall)

    elif task_id == "memory_compress_short":
        from app.services.memory_compress import compress_episode_summary

        summary, compressed = compress_episode_summary(
            task["text"], payload={}
        )
        ok = compressed is task["expect_compressed"] and summary == task["text"]
        assert ok
        record(task_id, passed=1.0 if ok else 0.0)

    else:
        pytest.fail(f"unknown golden task id: {task_id}")
