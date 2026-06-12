from app.runtime.state import create_initial_state, merge_state
from app.services.converge import NEXT_FORCE_WRITE, evaluate_convergence
from app.services.interaction_goal import goal_is_conversational_qa
from app.services.writing_intent_classifier import classify_writing_operator, goal_is_writing_manuscript_action
from app.services.writing_pending import (
    apply_confirm_pending_delivery,
    goal_looks_like_confirm_only,
    record_pending_writing_delivery,
    writing_false_promise_without_write,
)


def test_kickoff_body_operator():
    assert classify_writing_operator("开始写正文") == "kickoff_body"
    assert classify_writing_operator("写第一章") == "kickoff_body"
    assert classify_writing_operator("改大纲第三章") == "replot"
    assert goal_is_writing_manuscript_action("开始写正文")
    assert not goal_is_conversational_qa("开始写正文")


def test_replot_still_manuscript_action():
    assert classify_writing_operator("把第三章节奏改一下大纲") == "replot"
    assert goal_is_writing_manuscript_action("改大纲结局")
    assert not goal_is_conversational_qa("改大纲结局")


def test_false_promise_triggers_force_write():
    state = merge_state(
        create_initial_state(task_id="wp-1", input_payload={"writing_intent": {"enabled": True}}),
        reasoning_result={
            "summary": "好的，需要您确认后我立即开始撰写第一章正文。",
        },
        tool_results=[],
    )
    result = evaluate_convergence(state)
    assert result.next == NEXT_FORCE_WRITE
    assert result.reason == "writing_false_promise"


def test_confirm_inherits_pending_delivery():
    existing = create_initial_state(
        task_id="wp-2",
        input_payload={
            "pending_writing_delivery": {
                "operator": "kickoff_body",
                "goal": "按大纲写第一章正文",
                "interaction_goal": "delivery",
            },
            "writing_intent": {"enabled": True},
        },
    )
    merged = apply_confirm_pending_delivery(
        existing,
        {"goal": "确认", "confirm": True},
    )
    assert merged["goal"] == "按大纲写第一章正文"
    assert merged["writing_operator"] == "kickoff_body"
    assert merged["force_write_after_reads"] is True
    assert merged.get("pending_writing_delivery") is None


def test_record_pending_on_genuine_ask():
    state = merge_state(
        create_initial_state(
            task_id="wp-3",
            input_payload={
                "goal": "写第一章",
                "writing_intent": {"enabled": True},
                "writing_operator": "kickoff_body",
            },
        ),
        reasoning_result={
            "summary": "请补充主角姓名与时代背景，方可开始撰写。",
        },
        tool_results=[],
    )
    updated = record_pending_writing_delivery(state)
    pending = (updated.get("input_payload") or {}).get("pending_writing_delivery") or {}
    assert pending.get("operator") == "kickoff_body"
    assert pending.get("goal") == "写第一章"


def test_confirm_goal_text_only():
    assert goal_looks_like_confirm_only("确认")
    assert goal_looks_like_confirm_only("好的！")
    assert not goal_looks_like_confirm_only("开始写正文")
    assert writing_false_promise_without_write("需要您确认后我立即开始写第一章")
    assert not writing_false_promise_without_write(
        "请补充主角姓名与时代背景，方可开始撰写。"
    )
