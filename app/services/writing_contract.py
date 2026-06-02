"""Writing Generation Contract (WGC) — versioned expectations for artifact streaming."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

WGC_VERSION = "wgc/1"
ARTIFACT_TOOL_NAME = "submit_artifact"
ALLOWED_TOOL_ARG_KEYS = frozenset({"content"})

StreamCloseStatus = Literal["ok", "partial", "aborted", "failed"]
PartialCommitPolicy = Literal["allow_on_disconnect", "discard"]


@dataclass(frozen=True)
class WritingGenerationContract:
    """Immutable contract defaults for writing-purpose LLM streams."""

    version: str = WGC_VERSION
    delivery: Literal["tool", "json_text", "text_only"] = "tool"
    tool_name: str = ARTIFACT_TOOL_NAME
    allowed_tool_keys: frozenset[str] = field(default_factory=lambda: ALLOWED_TOOL_ARG_KEYS)
    partial_commit_policy: PartialCommitPolicy = "allow_on_disconnect"


def default_writing_contract() -> WritingGenerationContract:
    return WritingGenerationContract()


def contract_meta(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    c = default_writing_contract()
    out: dict[str, Any] = {
        "writing_contract_version": c.version,
        "writing_delivery": c.delivery,
        "writing_tool_name": c.tool_name,
    }
    if extra:
        out.update(extra)
    return out
