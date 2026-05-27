from unittest.mock import MagicMock

from app.services.tool_dag import execute_tool_stages, parse_tool_stages, _stages_from_dag


def test_parse_tool_stages_explicit():
    payload = {"tool_stages": [["calculator", "echo"], ["get_runtime_info"]]}
    stages = parse_tool_stages(payload, ["calculator", "echo", "get_runtime_info"])
    assert stages == [["calculator", "echo"], ["get_runtime_info"]]


def test_parse_tool_stages_fallback_sequential():
    stages = parse_tool_stages({}, ["a", "b"])
    assert stages == [["a"], ["b"]]


def test_stages_from_dag_layers():
    dag = {
        "nodes": [
            {"id": "c", "tool": "calculator"},
            {"id": "e", "tool": "echo"},
        ],
        "edges": [{"from": "c", "to": "e"}],
    }
    stages = _stages_from_dag(dag, ["calculator", "echo"])
    assert stages[0] == ["calculator"]
    assert stages[1] == ["echo"]


def test_execute_tool_stages_parallel_order():
    order: list[str] = []

    def invoke(name: str, _state):
        order.append(name)
        return {"tool": name, "result": {"ok": True}}

    stages = [["calculator", "echo"]]
    results = execute_tool_stages({}, stages, invoke_fn=invoke)
    assert len(results) == 2
    assert {r["tool"] for r in results} == {"calculator", "echo"}
    assert set(order) == {"calculator", "echo"}
