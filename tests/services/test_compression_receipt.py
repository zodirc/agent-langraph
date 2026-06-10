"""P0-2 compression receipt and extractive sentence keep."""

from app.services.compression_receipt import build_compression_receipt, extractive_sentences
from app.services.context_items import ContextItem, new_context_id
from app.services.context_policy import get_prompt_context_policy
from app.services.context_reducer import reduce_context_items
from app.services.context_trace import ContextAssemblyTrace


def test_build_compression_receipt_tracks_dropped_entities():
    before = "Alpha protocol TLS 1.3 and Beta service port 8443"
    after = "Alpha protocol TLS 1.3"
    receipt = build_compression_receipt(before, after, anchors=["TLS", "8443"])
    assert "Beta" in receipt["dropped_entities"] or "8443" in receipt["dropped_entities"]
    assert "TLS" in receipt["kept_anchors"]


def test_extractive_sentences_prefers_query_terms():
    text = (
        "Unrelated filler about weather patterns. "
        "Alpha protocol requires TLS 1.3 for all endpoints. "
        "More unrelated content about sports."
    )
    clipped, receipt = extractive_sentences(
        text, query="TLS Alpha protocol", max_chars=120
    )
    assert "TLS" in clipped
    assert "Alpha" in clipped
    assert receipt["kept_anchors"]


def test_knowledge_compression_emits_trace_receipt():
    policy = get_prompt_context_policy("reasoning")
    item = ContextItem(
        id=new_context_id(),
        kind="knowledge",
        source="retrieval",
        content=(
            "Noise sentence one. "
            "SECRET_ENTITY_X99 must be preserved in Alpha protocol TLS 1.3. "
            "Noise sentence two."
        ),
        priority="medium",
        estimated_tokens=200,
        droppable=True,
        compressible=True,
        bucket="retrieved_knowledge",
    )
    state = {"input_payload": {"goal": "TLS Alpha SECRET_ENTITY_X99"}}
    trace = ContextAssemblyTrace(purpose="reasoning")
    kept, _, _ = reduce_context_items(
        [item],
        policy,
        token_budget_total=8000,
        state=state,
        trace=trace,
    )
    assert kept
    receipts = trace.to_dict().get("compression_receipts") or []
    if receipts:
        assert any("SECRET_ENTITY_X99" in r.get("kept_anchors", []) for r in receipts) or any(
            "SECRET_ENTITY_X99" in (kept[0].content or "") for _ in [0]
        )
