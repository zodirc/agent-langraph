from app.domain.packs.base import DomainPack

ANALYSIS_PACK = DomainPack(
    name="analysis",
    description="General analysis and reasoning for cross-domain questions",
    tools=["echo"],
    planning_hints=["gather_context", "analyze", "conclude"],
    system_prompt=(
        "You are a general analysis agent. Synthesize available information and "
        "produce clear, structured conclusions."
    ),
    risk_level="LOW",
    metadata={
        "domains": ["analysis", "qa", "general"],
        "capabilities": ["analysis", "reasoning", "hypothesis", "exploration"],
    },
)
