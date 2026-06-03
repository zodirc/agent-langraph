from app.services.skill_output_validator import validate_skill_output


def test_require_summary_fails_when_short():
    state = {
        "skill_id": "test",
        "skill_runtime_policy": {
            "resolved_output_contract": {"require_summary": True, "strict": True},
        },
        "reasoning_result": {"summary": "ok"},
    }
    result = validate_skill_output(state)
    assert not result.passed
    assert result.issues


def test_passes_with_long_summary():
    state = {
        "skill_id": "test",
        "skill_runtime_policy": {
            "resolved_output_contract": {"require_summary": True},
        },
        "reasoning_result": {"summary": "A" * 40},
    }
    result = validate_skill_output(state)
    assert result.passed
