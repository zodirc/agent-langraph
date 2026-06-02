from app.nodes.tool_node import tool_execution_node
from app.runtime.router import route_after_planning, route_after_tool, should_reflect
from app.runtime.state import TaskStatus, create_initial_state, merge_state


def test_route_skips_retrieval_when_flagged():
    state = merge_state(
        create_initial_state(input_payload={"goal": "hi"}),
        plan=["execute_tools", "reason_and_answer"],
        selected_tools=["calculator"],
        skip_retrieval=True,
    )
    assert route_after_planning(state) == "tool_execution"


def test_route_skips_retrieval_to_reasoning_when_no_tools():
    state = merge_state(
        create_initial_state(input_payload={"goal": "hello"}),
        plan=["reason_and_answer"],
        selected_tools=[],
        skip_retrieval=True,
    )
    assert route_after_planning(state) == "reasoning"


def test_route_planning_failure_retries_then_dlq():
    state = merge_state(
        create_initial_state(input_payload={"goal": "x"}),
        status=TaskStatus.FAILED.value,
        retry_count=1,
    )
    assert route_after_planning(state) == "planning"

    state = merge_state(state, retry_count=3)
    assert route_after_planning(state) == "dead_letter"


def test_route_after_planning_mission_act_continues_pipeline(base_state):
    state = merge_state(
        base_state,
        input_payload={
            **base_state["input_payload"],
            "mission": {"kind": "writing", "total_target_chars": 8000},
            "writing_intent": {"enabled": True, "action": "write_outline"},
        },
        execution_mode="mission",
        mission_step=1,
        skip_retrieval=False,
    )
    assert route_after_planning(state) == "retrieval"


def test_route_after_planning_mission_contract_ends_single_graph(base_state):
    state = merge_state(
        base_state,
        input_payload={
            **base_state["input_payload"],
            "mission": {
                "kind": "writing",
                "total_target_chars": 1200000,
                "step_policy": {"chars_per_step": 4000},
            },
        },
        execution_mode="mission",
    )
    assert route_after_planning(state) == "end"


def test_route_after_tool_non_retryable_skips_tool_loop(base_state):
    state = merge_state(
        base_state,
        selected_tools=["read_text_artifact"],
        input_payload={
            **base_state["input_payload"],
            "tool_params": {"read_text_artifact": {"filename": "missing-outline.txt"}},
        },
    )
    failed = tool_execution_node(state)
    assert failed["status"] == TaskStatus.TOOL_FAILED.value
    assert route_after_tool(failed) == "reasoning"
    assert route_after_planning(failed) == "reasoning"


def test_route_after_tool_retryable_still_retries_tool_execution(base_state):
    state = merge_state(
        base_state,
        selected_tools=["missing_tool"],
        retry_count=0,
        status=TaskStatus.TOOL_FAILED.value,
        current_node="tool_execution",
        errors=["tool_execution: boom"],
        audit_log=[{"node": "tool_execution", "action": "error", "detail": "boom"}],
    )
    assert route_after_tool(state) == "tool_execution"


def test_should_reflect_skips_when_mission_runtime(base_state):
    state = merge_state(
        base_state,
        input_payload={
            **base_state["input_payload"],
            "mission": {"kind": "writing"},
            "writing_intent": {"enabled": True},
        },
        execution_mode="mission",
    )
    assert should_reflect(state) is False
