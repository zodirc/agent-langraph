from __future__ import annotations

import logging

from app.services.knowledge_store import get_knowledge_store

logger = logging.getLogger(__name__)

DEFAULT_DOCUMENTS = [
    {
        "title": "LangGraph Agent Runtime Overview",
        "content": (
            "This project uses LangGraph as the execution runtime with nodes for planning, "
            "retrieval, tool execution, reasoning, policy checks, human review, output, "
            "and memory writeback."
        ),
        "metadata": {"source": "builtin", "topic": "architecture"},
    },
    {
        "title": "Policy Engine Rules",
        "content": (
            "Policy results include CONTINUE, REVIEW, REJECT, and ESCALATE. "
            "HIGH risk requires human review. CRITICAL risk is auto rejected."
        ),
        "metadata": {"source": "builtin", "topic": "policy"},
    },
    {
        "title": "Knowledge Retrieval Strategy",
        "content": (
            "Knowledge retrieval uses hybrid search combining ChromaDB vector similarity "
            "and SQLite keyword matching merged with reciprocal rank fusion."
        ),
        "metadata": {"source": "builtin", "topic": "retrieval"},
    },
]


def seed_default_knowledge() -> int:
    store = get_knowledge_store()
    if store.count() > 0:
        return 0
    created = 0
    for doc in DEFAULT_DOCUMENTS:
        store.upsert_document(
            title=doc["title"],
            content=doc["content"],
            metadata=doc["metadata"],
        )
        created += 1
    logger.info("Seeded %s knowledge documents", created, extra={"seeded": created})
    return created
