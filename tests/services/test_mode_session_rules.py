from app.runtime.state import create_initial_state
from app.services.mode_router import refine_mode_for_session_switch, resolve_target_mode


def test_engineering_to_qa_followup():
    intent, mode, note = refine_mode_for_session_switch(
        intent_kind="qa",
        target_mode="qa_mode",
        current_mode="engineering_mode",
        confidence=0.6,
        goal="为什么这个游戏用 grid 而不是 canvas？",
        min_kind_score=0.35,
    )
    assert mode == "qa_mode"
    assert note == "engineering_to_qa_followup"


def test_qa_to_engineering_explicit_delivery():
    intent, mode, note = refine_mode_for_session_switch(
        intent_kind="code",
        target_mode="engineering_mode",
        current_mode="qa_mode",
        confidence=0.5,
        goal="请直接生成项目文件，落盘可编译的 C++ 源码",
        min_kind_score=0.35,
    )
    assert mode == "engineering_mode"
    assert note == "qa_to_engineering_explicit_delivery"


def test_resolve_with_prior_engineering_mode():
    state = create_initial_state(
        task_id="mode-swap-1",
        input_payload={
            "goal": "为什么这样实现",
            "current_mode": "engineering_mode",
            "route_audit": {"inferred_kind": "qa", "kind_confidence": 0.7},
        },
    )
    res = resolve_target_mode(state)
    assert res.target_mode == "qa_mode"
    assert (
        "engineering_to_qa_followup" in res.mode_switch_reason
        or "engineering_to_conversational_qa" in res.mode_switch_reason
    )
