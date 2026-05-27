from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx

from app.config.settings import settings
from app.services.graph_runner import get_graph_runner
from app.services.state_store import get_state_store


def _local_run(goal: str, *, supervisor: bool = False) -> dict[str, Any]:
    runner = get_graph_runner()
    payload: dict[str, Any] = {"goal": goal, "risk_level": "LOW"}
    mode = "supervisor" if supervisor else "single"
    if supervisor:
        payload["domains"] = ["document", "analysis"]
    state = runner.start_task(
        user_id="cli",
        task_type="supervisor" if supervisor else "qa",
        input_payload=payload,
        execution_mode=mode,
    )
    return {
        "task_id": state["task_id"],
        "status": state["status"],
        "final_answer": state.get("final_answer"),
    }


def _remote_request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    base = settings.CLI_API_BASE_URL.rstrip("/")
    headers = {}
    if settings.CLI_API_KEY:
        headers["X-API-Key"] = settings.CLI_API_KEY
    return httpx.request(method, f"{base}{path}", headers=headers, timeout=120.0, **kwargs)


def cmd_run(args: argparse.Namespace) -> int:
    if args.local:
        result = _local_run(args.goal, supervisor=args.supervisor)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    response = _remote_request(
        "POST",
        "/supervisor/tasks" if args.supervisor else "/tasks",
        json={"task_type": "supervisor" if args.supervisor else "qa", "input_payload": {"goal": args.goal}},
    )
    response.raise_for_status()
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    if args.local:
        state = get_state_store().load(args.task_id)
        if not state:
            print(f"Task not found: {args.task_id}", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "task_id": state["task_id"],
                    "status": state["status"],
                    "current_node": state["current_node"],
                    "node_history": state.get("node_history", []),
                    "review_required": state.get("review_required"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    response = _remote_request("GET", f"/tasks/{args.task_id}/status")
    response.raise_for_status()
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    return 0


def cmd_result(args: argparse.Namespace) -> int:
    if args.local:
        state = get_state_store().load(args.task_id)
        if not state:
            print(f"Task not found: {args.task_id}", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "task_id": state["task_id"],
                    "status": state["status"],
                    "final_answer": state.get("final_answer"),
                    "structured_output": state.get("structured_output"),
                    "artifacts": state.get("artifacts"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    response = _remote_request("GET", f"/tasks/{args.task_id}/result")
    response.raise_for_status()
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Agent LangGraph CLI (§8.2)")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Run against in-process runtime instead of HTTP API",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Create and execute a task")
    run_p.add_argument("goal", help="Task goal / question")
    run_p.add_argument("--supervisor", action="store_true", help="Use supervisor mode")
    run_p.set_defaults(func=cmd_run)

    status_p = sub.add_parser("status", help="Query task status")
    status_p.add_argument("task_id")
    status_p.set_defaults(func=cmd_status)

    result_p = sub.add_parser("result", help="Query task result")
    result_p.add_argument("task_id")
    result_p.set_defaults(func=cmd_result)

    args = parser.parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
