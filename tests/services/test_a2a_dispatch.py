from app.domain.agent_message import AgentMessage
from app.services.a2a_dispatch import (
    decompose_to_agent_messages,
    resolve_pack_for_capability,
    runtime_agent_card,
)


def test_runtime_agent_card_has_capabilities():
    card = runtime_agent_card()
    assert card.agent_id
    assert len(card.capabilities) >= 1
    assert "analysis" in card.domains or "analysis" in card.capabilities


def test_resolve_pack_for_capability():
    assert resolve_pack_for_capability("analysis") == "analysis"
    assert resolve_pack_for_capability("hypothesis") == "analysis"


def test_decompose_returns_a2a_messages():
    subtasks = decompose_to_agent_messages("compare two options", ["analysis"])
    assert len(subtasks) >= 1
    assert subtasks[0].get("required_capability")
    assert subtasks[0].get("a2a_message")
