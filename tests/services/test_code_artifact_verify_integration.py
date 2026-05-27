from unittest.mock import patch

from app.services.code_artifact_pipeline import ensure_code_artifacts_quality
from app.services.code_verify.models import VerifyResult


VALID_CPP = """#include <iostream>
int main() {
    return 0;
}
"""


@patch("app.services.code_artifact_pipeline.repair_artifacts_via_llm")
@patch("app.services.code_verify.pipeline.verify_code_artifacts")
def test_ensure_quality_marks_verify_ok(mock_verify, mock_repair):
    mock_verify.return_value = [
        VerifyResult(ok=True, backend="cpp", stage="compile", language="cpp")
    ]
    mock_repair.return_value = None

    result = ensure_code_artifacts_quality(
        {
            "summary": "code",
            "structured": {
                "artifacts": [{"kind": "code", "language": "cpp", "content": VALID_CPP}]
            },
        },
        {
            "task_id": "t-int",
            "input_payload": {"route_audit": {"inferred_kind": "code"}, "goal": "cpp"},
        },
        goal="cpp sample",
        budget_ctx=None,
    )
    structured = result["structured"]
    assert structured.get("code_verify_ok") is True


@patch("app.services.code_artifact_pipeline.repair_artifacts_via_llm")
@patch("app.services.code_verify.pipeline.verify_code_artifacts")
def test_ensure_quality_verify_failed(mock_verify, mock_repair):
    mock_verify.return_value = [
        VerifyResult(
            ok=False,
            backend="cpp",
            stage="compile",
            language="cpp",
            stderr="error: expected '}'",
        )
    ]
    mock_repair.return_value = None

    result = ensure_code_artifacts_quality(
        {
            "summary": "code",
            "structured": {
                "artifacts": [{"kind": "code", "language": "cpp", "content": "int x;"}]
            },
        },
        {
            "task_id": "t-int-fail",
            "input_payload": {"route_audit": {"inferred_kind": "code"}},
        },
    )
    structured = result["structured"]
    assert structured.get("code_verify_failed") is True
    mock_repair.assert_called()
