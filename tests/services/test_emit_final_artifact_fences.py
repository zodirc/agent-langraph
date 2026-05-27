from __future__ import annotations

from unittest.mock import patch


def test_emit_final_artifact_fences_skips_verify_message_without_code_artifacts():
    from app.services.reasoning_trace import emit_final_artifact_fences

    emitted: list[str] = []

    with patch(
        "app.services.reasoning_trace.answer_stream_enabled",
        return_value=True,
    ), patch(
        "app.services.reasoning_trace.report_answer_delta",
        side_effect=lambda **kw: emitted.append(kw.get("text") or ""),
    ):
        emit_final_artifact_fences(
            {
                "summary": "海贼王是尾田荣一郎的漫画。",
                "artifacts": [],
            }
        )
        emit_final_artifact_fences(
            {
                "summary": "说明",
                "artifacts": [{"kind": "text", "content": "not code"}],
            }
        )

    assert emitted == []


def test_emit_final_artifact_fences_shows_verify_message_when_verify_failed():
    from app.services.reasoning_trace import emit_final_artifact_fences

    emitted: list[str] = []

    with patch(
        "app.services.reasoning_trace.answer_stream_enabled",
        return_value=True,
    ), patch(
        "app.services.reasoning_trace.report_answer_delta",
        side_effect=lambda **kw: emitted.append(kw.get("text") or ""),
    ):
        emit_final_artifact_fences(
            {
                "code_verify_failed": True,
                "artifacts": [
                    {"kind": "code", "language": "cpp", "content": "int main(){}"}
                ],
            }
        )

    assert len(emitted) == 1
    assert "编译校验" in emitted[0]


def test_should_process_code_artifacts_false_for_general_qa_state():
    from app.services.code_artifact_pipeline import should_process_code_artifacts

    state = {
        "input_payload": {
            "goal": "你知道海贼王吗？",
            "route_audit": {
                "inferred_kind": "qa",
                "planned_route": "reasoning_only",
            },
        },
    }
    assert should_process_code_artifacts(state) is False
