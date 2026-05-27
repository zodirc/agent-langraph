from app.runtime.state import TaskStatus, merge_state
from app.services.conversation_context import (
    apply_conversation_history,
    conversation_history_for_llm,
    conversation_history_from_state,
    finalize_turn_history,
    is_system_surface_message,
    persist_turn_draft_answer,
    resolve_turn_surface_answer,
    should_persist_assistant_turn,
)


def test_conversation_history_prefers_payload_when_longer():
    state = {
        "conversation_history": [{"role": "assistant", "content": "old", "at": "1"}],
        "input_payload": {
            "conversation_history": [
                {"role": "user", "content": "hi", "at": "1"},
                {"role": "assistant", "content": "old", "at": "2"},
            ]
        },
    }
    hist = conversation_history_from_state(state)
    assert hist[0]["role"] == "user"
    assert len(hist) == 2


def test_finalize_syncs_payload_and_appends_assistant_from_reasoning_draft():
    state = merge_state(
        {
            "task_id": "t1",
            "session_id": "t1",
            "user_id": "u1",
            "task_type": "qa",
            "status": TaskStatus.POLICY_CHECKED.value,
            "input_payload": {
                "conversation_history": [{"role": "user", "content": "big int add", "at": "1"}]
            },
            "reasoning_result": {
                "summary": "```cpp\nint main(){}",
                "confidence": 0.45,
                "structured": {"parser_fallback": True},
            },
        }
    )
    out = finalize_turn_history(state)
    hist = out["conversation_history"]
    assert hist[-1]["role"] == "assistant"
    assert "cpp" in hist[-1]["content"]
    assert out["input_payload"]["conversation_history"] == hist
    assert out["final_answer"]


def test_persist_turn_draft_answer_from_reasoning():
    state = {
        "task_id": "t1",
        "session_id": "t1",
        "reasoning_result": {"summary": "answer text"},
        "final_answer": None,
    }
    out = persist_turn_draft_answer(state)
    assert out["final_answer"] == "answer text"


def test_resolve_turn_surface_answer_prefers_final_answer():
    state = {
        "final_answer": "final",
        "reasoning_result": {"summary": "draft"},
    }
    assert resolve_turn_surface_answer(state) == "final"


def test_conversation_history_for_llm_filters_system_boilerplate():
    history = [
        {"role": "user", "content": "大数相加", "at": "1"},
        {
            "role": "assistant",
            "content": "Task rejected. Policy result: REJECT",
            "at": "2",
        },
        {
            "role": "assistant",
            "content": "正常回答",
            "at": "3",
            "meta": {"surface": "user", "outcome": "completed"},
        },
    ]
    llm = conversation_history_for_llm(history)
    assert len(llm) == 2
    assert llm[-1]["content"] == "正常回答"


def test_should_not_persist_rejected_turn():
    state = {"status": TaskStatus.REJECTED.value}
    assert should_persist_assistant_turn(state, "some answer") is False


def test_is_system_surface_message():
    assert is_system_surface_message("Task rejected. Policy result: REJECT") is True
    assert is_system_surface_message("正常代码示例") is False


def test_resolve_turn_surface_answer_empty_when_rejected():
    state = {
        "status": TaskStatus.REJECTED.value,
        "final_answer": "Task rejected. Policy result: REJECT",
        "reasoning_result": {"summary": "真实内容"},
    }
    assert resolve_turn_surface_answer(state) == ""


def test_apply_conversation_history_updates_both_layers():
    state = {"task_id": "t1", "session_id": "t1", "input_payload": {}}
    hist = [{"role": "user", "content": "x", "at": "1"}]
    out = apply_conversation_history(state, hist)
    assert out["conversation_history"] == hist
    assert out["input_payload"]["conversation_history"] == hist
