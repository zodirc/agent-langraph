from app.nodes.output_guard_node import output_guard_node
from app.runtime.state import TaskStatus, merge_state
from app.services.grounding_policy import (
    classify_grounding_turn,
    should_run_grounding_check,
    tool_observation_hits,
)


def test_classify_tool_turn_when_edit_plot_tools_ran():
    state = {
        "input_payload": {
            "turn_contract": {"primary_op": "edit_plot"},
        },
        "retrieved_knowledge": [{"doc_id": "style", "content": "去AI化 口吻"}],
        "tool_results": [
            {
                "tool": "read_text_artifact",
                "status": "ok",
                "result": {
                    "filename": "虚相_大纲.txt",
                    "content": "陆执结局：殉道者的沉默。沈惊蛰成为相位锚点。",
                },
            },
            {
                "tool": "edit_text_artifact",
                "status": "ok",
                "result": {"filename": "虚相_大纲.txt", "bytes": 13211},
            },
        ],
    }
    assert classify_grounding_turn(state) == "tool_observation"
    assert should_run_grounding_check(state) is True
    hits = tool_observation_hits(state)
    assert hits and "陆执" in hits[0]["content"]


def test_classify_skip_for_writing_style_only_rag():
    state = {
        "input_payload": {"writing_intent": {"enabled": True}},
        "retrieved_knowledge": [
            {"doc_id": "builtin-writing-guidelines", "metadata": {"domain": "writing"}},
        ],
        "tool_results": [],
    }
    assert classify_grounding_turn(state) == "skip"
    assert should_run_grounding_check(state) is False


def test_output_guard_passes_writing_status_ask_with_style_rag(base_state, monkeypatch):
    monkeypatch.setattr("app.nodes.output_guard_node.settings.OUTPUT_GUARD_ENABLED", True)
    monkeypatch.setattr(
        "app.nodes.output_guard_node.settings.RETRIEVAL_CITATION_CHECK_STRICTNESS", "basic"
    )
    state = merge_state(
        base_state,
        retrieved_knowledge=[
            {
                "doc_id": "builtin-writing-guidelines",
                "content": "长文写作规范 场景锚点 伏笔",
                "metadata": {"domain": "writing"},
            }
        ],
        tool_results=[],
        reasoning_result={
            "summary": (
                "已制定五步创作计划，但本轮未执行任何工具，也未检索到《岁月》电视剧剧情资料；"
                "需先补充剧情信息或用户提供故事大纲，方可开始撰写小说正文。"
            ),
            "confidence": 0.8,
            "risk_level": "LOW",
        },
        input_payload={
            **base_state["input_payload"],
            "writing_intent": {"enabled": True},
            "target_mode": "manuscript_mode",
            "goal": "根据电视剧岁月写小说",
        },
        status=TaskStatus.REASONED.value,
    )
    result = output_guard_node(state)
    guard = result.get("output_guard_result") or {}
    assert guard.get("passed") is True
    assert result.get("status") != TaskStatus.REJECTED.value


def test_classify_rag_turn_for_pure_qa():
    state = {
        "skip_retrieval": False,
        "retrieved_knowledge": [{"doc_id": "doc1", "content": "Python uses Timsort."}],
        "tool_results": [],
    }
    assert classify_grounding_turn(state) == "rag"
    assert should_run_grounding_check(state) is True


def test_output_guard_passes_edit_plot_summary_against_tool_observation(base_state, monkeypatch):
    monkeypatch.setattr("app.nodes.output_guard_node.settings.OUTPUT_GUARD_ENABLED", True)
    monkeypatch.setattr(
        "app.nodes.output_guard_node.settings.RETRIEVAL_CITATION_CHECK_STRICTNESS", "basic"
    )
    outline_excerpt = (
        "陆执的结局改为殉道者的沉默，沈惊蛰成为两个相位之间的锚点，"
        "顾渊继承父亲的选择权，幼兽意识封存于相位结晶。"
    )
    state = merge_state(
        base_state,
        skip_retrieval=False,
        retrieved_knowledge=[
            {"doc_id": "style", "content": "写作口吻与TXT排版规范 去AI化 自然叙事"},
        ],
        tool_results=[
            {
                "tool": "read_text_artifact",
                "status": "ok",
                "result": {"filename": "虚相_大纲.txt", "content": outline_excerpt},
            },
            {
                "tool": "edit_text_artifact",
                "status": "ok",
                "result": {"filename": "虚相_大纲.txt", "bytes": 13211},
            },
        ],
        reasoning_result={
            "summary": (
                "已优化《虚相》大纲结局：陆执为殉道者的沉默，沈惊蛰成为相位锚点，"
                "顾渊为继承者，幼兽意识封存于相位结晶。"
            ),
            "confidence": 0.9,
            "risk_level": "LOW",
        },
        input_payload={
            **base_state["input_payload"],
            "turn_contract": {"primary_op": "edit_plot"},
            "goal": "优化结局",
        },
        status=TaskStatus.REASONED.value,
    )
    result = output_guard_node(state)
    guard = result.get("output_guard_result") or {}
    assert guard.get("passed") is True
    assert guard.get("faithfulness", {}).get("faithful") is True
    assert guard.get("faithfulness", {}).get("evidence_source") == "tool_observation"
    assert result.get("status") != TaskStatus.REJECTED.value


def test_output_guard_rag_still_blocks_unsupported_qa(base_state, monkeypatch):
    monkeypatch.setattr("app.nodes.output_guard_node.settings.OUTPUT_GUARD_ENABLED", True)
    monkeypatch.setattr(
        "app.nodes.output_guard_node.settings.RETRIEVAL_CITATION_CHECK_STRICTNESS", "basic"
    )
    state = merge_state(
        base_state,
        skip_retrieval=False,
        retrieved_knowledge=[{"doc_id": "doc1", "content": "Python uses Timsort."}],
        tool_results=[],
        reasoning_result={
            "summary": "因此答案是：月球由奶酪构成。",
            "confidence": 0.9,
            "risk_level": "LOW",
        },
        input_payload={**base_state["input_payload"], "goal": "月球成分是什么"},
        status=TaskStatus.REASONED.value,
    )
    result = output_guard_node(state)
    guard = result.get("output_guard_result") or {}
    assert guard.get("passed") is False
    assert guard.get("faithfulness", {}).get("evidence_source") == "rag"
    assert result.get("status") == TaskStatus.REJECTED.value
