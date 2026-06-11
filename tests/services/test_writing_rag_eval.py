from app.services.writing_rag_eval import (
    compare_rag_ab_delta,
    score_anti_ai_voice,
    score_format_compliance,
    score_writing_compliance,
)


def test_format_compliance_detects_chapter_footer():
    text = "第一章\n\n" + "江湖夜雨十年灯。" * 20 + "\n\n（第1章完）"
    assert score_format_compliance(text) >= 0.8


def test_anti_ai_penalizes_cliches():
    bad = "众所周知，综上所述，作为AI我根据您的要求写作。"
    good = "雨落瓦当。她推门而入，袖里藏着未寄出的信。"
    assert score_anti_ai_voice(good) > score_anti_ai_voice(bad)


def test_compare_rag_ab_delta():
    with_rag = score_writing_compliance("段落\n\n（第2章完）", guidelines_excerpt="章末（第N章完）")
    without_rag = score_writing_compliance("众所周知，综上所述。")
    delta = compare_rag_ab_delta(with_rag, without_rag)
    assert delta["overall_delta"] > 0
