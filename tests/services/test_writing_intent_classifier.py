from app.services.writing_intent_classifier import classify_writing_operator


def test_classify_append():
    assert classify_writing_operator("续写下一章", {"input_payload": {"target_mode": "manuscript_mode"}}) == "append"


def test_classify_rewrite():
    assert (
        classify_writing_operator(
            "重写小说，内容太少",
            {"input_payload": {"target_mode": "manuscript_mode"}},
        )
        == "rewrite"
    )


def test_classify_replot():
    assert (
        classify_writing_operator(
            "改结局让人物更合理",
            {"input_payload": {"target_mode": "manuscript_mode"}},
        )
        == "replot"
    )


def test_classify_none_for_pure_qa():
    assert classify_writing_operator("什么是光合作用？", {"input_payload": {}}) is None
