from app.runtime.state import create_initial_state, merge_state
from app.runtime.router import route_after_planning
from app.services.memory_query import build_memory_search_query
from app.services.writing_knowledge import (
    enrich_retrieval_query_for_writing,
    resolve_writing_guidelines_excerpt,
)


def test_enrich_retrieval_query_for_writing():
    state = merge_state(
        create_initial_state(input_payload={"goal": "续写第三章"}),
        input_payload={
            "goal": "续写第三章",
            "writing_intent": {"enabled": True, "action": "append_body"},
        },
    )
    q = enrich_retrieval_query_for_writing(state, "续写第三章")
    assert "写作规范" in q


def test_route_writing_goes_to_retrieval_when_not_skipped(base_state):
    state = merge_state(
        base_state,
        plan=["append chapter via writing"],
        selected_tools=[],
        skip_retrieval=False,
        input_payload={
            **base_state["input_payload"],
            "writing_intent": {"enabled": True, "action": "append_body"},
        },
    )
    assert route_after_planning(state) == "retrieval"


def test_resolve_writing_guidelines_from_file():
    state = create_initial_state(input_payload={"goal": "写小说"})
    excerpt = resolve_writing_guidelines_excerpt(state, query_hint="续写")
    assert excerpt
    assert "写作" in excerpt or "章节" in excerpt
    assert "去 AI" in excerpt or "去AI" in excerpt or "AI腔" in excerpt
    assert "TXT" in excerpt or "排版" in excerpt


def test_enrich_retrieval_query_includes_voice_and_format_bias():
    state = merge_state(
        create_initial_state(input_payload={"goal": "写正文"}),
        input_payload={"writing_intent": {"enabled": True}},
    )
    q = enrich_retrieval_query_for_writing(state, "写正文")
    assert "去AI化" in q
    assert "TXT排版" in q


def test_build_memory_search_query_includes_writing_bias(base_state):
    state = merge_state(
        base_state,
        input_payload={
            **base_state["input_payload"],
            "goal": "继续写下去",
            "writing_intent": {"enabled": True, "action": "append_body"},
        },
    )
    q = build_memory_search_query(state)
    assert "写作规范" in q
