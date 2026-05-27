from tests.eval.eval_thresholds import check_rag_thresholds


def test_thresholds_pass():
    snapshot = {
        "rag_recall_exact": {"recall@5": 1.0},
        "rag_mrr_first_rank": {"mrr": 1.0},
        "rag_faithful_answer": {"faithfulness": 0.9},
    }
    assert not check_rag_thresholds(
        snapshot, min_recall=0.8, min_mrr=0.5, min_faithfulness=0.75
    )


def test_thresholds_fail_recall():
    snapshot = {"rag_recall_exact": {"recall@5": 0.5}}
    errors = check_rag_thresholds(snapshot, min_recall=0.8)
    assert any("recall" in e for e in errors)


def test_thresholds_ignore_negative_faithfulness_controls():
    snapshot = {
        "rag_faithful_answer_faithfulness": {"faithfulness": 0.95},
        "rag_unfaithful_answer_faithfulness": {"faithfulness": 0.1},
        "rag_llm_faithfulness_good": {"score": 0.92},
        "rag_llm_faithfulness_bad": {"score": 0.2},
    }
    assert not check_rag_thresholds(snapshot, min_faithfulness=0.75)
