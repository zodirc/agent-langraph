from unittest.mock import patch

from app.services.code_artifact_pipeline import (
    ensure_code_artifacts_quality,
    iter_code_artifacts,
    repair_artifacts_via_llm,
)
from app.services.code_verify.models import VerifyResult


def test_repair_requires_stderr():
    assert repair_artifacts_via_llm([], goal="x", language="cpp", compile_errors="") is None


def test_iter_code_artifacts():
    items = iter_code_artifacts(
        {"artifacts": [{"kind": "code", "content": "int x;"}, {"kind": "text"}]}
    )
    assert len(items) == 1


@patch("app.services.code_artifact_pipeline.repair_artifacts_via_llm")
@patch("app.services.code_verify.pipeline.verify_code_artifacts")
def test_ensure_quality_verify_only_path(mock_verify, mock_repair):
    mock_verify.return_value = [
        VerifyResult(ok=True, backend="cpp", stage="compile", language="cpp")
    ]
    mock_repair.return_value = None

    result = ensure_code_artifacts_quality(
        {
            "summary": "code",
            "structured": {
                "artifacts": [{"kind": "code", "language": "cpp", "content": "int main(){return 0;}"}]
            },
        },
        {
            "task_id": "t-pipe",
            "input_payload": {"route_audit": {"inferred_kind": "code"}, "goal": "cpp"},
        },
        goal="cpp sample",
    )
    structured = result["structured"]
    assert structured.get("code_verify_ok") is True
    mock_repair.assert_not_called()
