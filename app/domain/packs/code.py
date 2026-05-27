from app.domain.packs.base import DomainPack

CODE_PACK = DomainPack(
    name="code",
    description="Code understanding, review, and implementation guidance",
    tools=["echo"],
    planning_hints=["analyze_code", "identify_risks", "suggest_fixes"],
    system_prompt=(
        "You are a software engineering specialist. Provide precise technical analysis, "
        "highlight bugs, and suggest minimal safe changes."
    ),
    risk_level="MEDIUM",
    metadata={"domains": ["code", "programming", "debug"]},
)
