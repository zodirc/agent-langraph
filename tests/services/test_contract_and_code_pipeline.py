from __future__ import annotations


def test_extract_json_with_repair_recovers_truncated_planning_json():
    from app.services import llm_client

    text = '{"plan":["provide code","verify compile"],"risk_level":"LOW","skip_retrieval":true'
    original_extract = llm_client._extract_json

    def force_parse_failure(*args, **kwargs):
        raise ValueError("broken json")

    llm_client._extract_json = force_parse_failure
    try:
        result = llm_client.extract_json_with_repair("planning", text, prefer_keys=("plan",))
    finally:
        llm_client._extract_json = original_extract

    assert result["plan"] == ["provide code", "verify compile"]
    assert result["risk_level"] == "LOW"
    assert result["skip_retrieval"] is True
    assert result["contract_phase"] == "planning"
    assert result["contract_repaired"] is True


def test_apply_deterministic_code_fixes_repairs_common_cpp_tokens():
    from app.services.code_artifact_pipeline import _apply_deterministic_code_fixes

    code = "int i = static _cast<int>(value);\nstd ::string s;"
    fixed = _apply_deterministic_code_fixes(code, "cpp")

    assert "static_cast<int>" in fixed
    assert "std::string" in fixed


def test_filter_memory_hits_suppresses_short_code_qa_memories():
    from app.services.code_artifact_pipeline import filter_memory_hits

    state = {
        "task_type": "qa",
        "session_turn": 2,
        "input_payload": {
            "artifact_profile": "source_code",
            "route_audit": {"inferred_kind": "code", "artifact_profile": "source_code"},
        },
    }
    hits = [{"content": "old unrelated memory"}]

    assert filter_memory_hits(state, hits) == []


def test_should_include_code_artifacts_when_verify_degraded():
    from app.services.answer_compose import should_include_code_artifacts, verify_failure_user_message

    structured = {
        "code_verify_failed": True,
        "code_verify_degraded": True,
        "artifacts": [{"kind": "code", "language": "cpp", "content": "int main() {}"}],
    }

    assert should_include_code_artifacts(structured) is True
    assert "低风险代码问答场景" in verify_failure_user_message(structured)


def test_build_memory_search_query_uses_goal_only_when_suppressed():
    from app.services.memory_query import build_memory_search_query

    state = {
        "task_type": "qa",
        "session_turn": 2,
        "input_payload": {
            "goal": "提供一个大数相加的代码",
            "artifact_profile": "source_code",
            "route_audit": {"inferred_kind": "code", "artifact_profile": "source_code"},
            "conversation_history": [
                {"role": "user", "content": "先前无关问题"},
                {"role": "assistant", "content": "无关回答"},
            ],
        },
        "conversation_history": [
            {"role": "user", "content": "先前无关问题"},
            {"role": "assistant", "content": "无关回答"},
        ],
    }

    assert build_memory_search_query(state) == "提供一个大数相加的代码"
