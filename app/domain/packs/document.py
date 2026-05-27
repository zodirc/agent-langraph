from app.domain.packs.base import DomainPack

DOCUMENT_PACK = DomainPack(
    name="document",
    description="Document parsing, summarization, and knowledge extraction",
    tools=["summarize_text", "echo"],
    planning_hints=["retrieve_context", "summarize_document", "extract_key_points"],
    system_prompt=(
        "You are a document processing specialist. Focus on accurate summaries, "
        "structure extraction, and evidence-backed conclusions from text."
    ),
    risk_level="LOW",
    metadata={"domains": ["document", "text", "report"]},
)
