"""Agent registry tests."""

from __future__ import annotations

from app.domain.agent_message import AgentCard
from app.services.agent_registry import AgentRegistry


def test_register_and_discover(tmp_path) -> None:
    db = str(tmp_path / "registry.db")
    reg = AgentRegistry(db_path=db)
    card = AgentCard(
        agent_id="worker-a",
        name="Worker A",
        description="test",
        capabilities=["analysis"],
        domains=["analysis"],
    )
    reg.register(card, "http://127.0.0.1:9001", ttl_sec=600)
    matches = reg.discover("analysis")
    assert len(matches) == 1
    assert matches[0][1] == "http://127.0.0.1:9001"
