from app.services.policy_engine import get_policy_engine


def test_policy_engine_continue():
    decision = get_policy_engine().evaluate(
        reasoning_result={"confidence": 0.9, "risk_level": "LOW"},
        user_role="user",
    )
    assert decision.result == "CONTINUE"


def test_policy_engine_review_high_risk():
    decision = get_policy_engine().evaluate(
        reasoning_result={"confidence": 0.9, "risk_level": "HIGH"},
    )
    assert decision.result == "REVIEW"


def test_policy_engine_code_verify_failed_review():
    decision = get_policy_engine().evaluate(
        reasoning_result={
            "confidence": 0.95,
            "risk_level": "LOW",
            "structured": {"code_verify_failed": True},
        },
    )
    assert decision.result == "REVIEW"
    assert "compile verification" in decision.reason


def test_policy_engine_parser_fallback_continues_despite_low_confidence():
    decision = get_policy_engine().evaluate(
        reasoning_result={
            "confidence": 0.45,
            "risk_level": "MEDIUM",
            "structured": {"parser_fallback": True},
        },
    )
    assert decision.result == "CONTINUE"
    assert "Parser-format" in decision.reason
