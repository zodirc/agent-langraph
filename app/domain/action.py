"""Unified Action model — the single executable currency of the agent loop.

Design goal: eliminate the historical translation chain
(intent -> anchor -> writing command -> edit_spec -> tool params) that lost
information between layers. An ``Action`` carries fully-resolved parameters and
maps 1:1 onto a concrete leaf capability (artifact tools, retrieval, registered
tools, engineering). The planner emits an ``Action`` directly; the act node
executes it without any further re-interpretation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

ActionType = Literal[
    "answer",          # produce the final natural-language answer (reasoning)
    "retrieve",        # query knowledge / RAG
    "read_artifact",   # read (optionally a line range of) a document file
    "write_artifact",  # create/overwrite a whole document file
    "edit_artifact",   # precise in-place edit of an existing document file
    "run_tool",        # invoke a registered tool by name
    "run_code",        # engineering / code execution path
]

# Action types that finish the turn on success (no further loop iteration needed
# unless the convergence gate explicitly disagrees).
_TERMINAL_TYPES = {"answer"}

# Map action type -> the canonical backend tool name (when one exists). Actions
# without a registry tool (answer / run_code) are handled by dedicated nodes.
ACTION_TOOL_NAMES: dict[str, str] = {
    "retrieve": "knowledge_search",
    "read_artifact": "read_text_artifact",
    "write_artifact": "write_text_artifact",
    "edit_artifact": "edit_text_artifact",
}


@dataclass
class Action:
    """A single, fully-resolved unit of work.

    ``params`` are already in the exact shape the backend tool expects, so no
    downstream component needs to re-derive filenames, anchors, or edit ranges.
    """

    type: ActionType
    params: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    # The agent's own signal that this action is expected to satisfy the turn
    # goal. The convergence gate treats it as a hint, not a command.
    completes_turn: bool = False
    confidence: float = 0.0
    source: str = "llm"  # llm | structural | explicit

    def __post_init__(self) -> None:
        if self.type not in ACTION_TOOL_NAMES and self.type not in ("answer", "run_code", "run_tool"):
            raise ValueError(f"unknown action type: {self.type!r}")

    @property
    def is_terminal(self) -> bool:
        return self.type in _TERMINAL_TYPES or self.completes_turn

    @property
    def tool_name(self) -> Optional[str]:
        """Registry tool backing this action, or None for node-handled actions."""
        return ACTION_TOOL_NAMES.get(self.type)

    def as_tool_call(self, task_id: str) -> Optional[dict[str, Any]]:
        """Render this action as a tool invocation, or None if node-handled.

        ``task_id`` is injected so artifact actions resolve to the right
        workspace without the caller re-deriving it.
        """
        name = self.tool_name
        if name is None:
            return None
        params = dict(self.params)
        if self.type in ("read_artifact", "write_artifact", "edit_artifact"):
            params.setdefault("task_id", task_id)
        return {"name": name, "params": params}

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "params": dict(self.params),
            "rationale": self.rationale,
            "completes_turn": self.completes_turn,
            "confidence": self.confidence,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Action":
        action_type = str(data.get("type") or "").strip()
        return cls(
            type=action_type,  # type: ignore[arg-type]
            params=dict(data.get("params") or {}),
            rationale=str(data.get("rationale") or ""),
            completes_turn=bool(data.get("completes_turn", False)),
            confidence=float(data.get("confidence") or 0.0),
            source=str(data.get("source") or "llm"),
        )


def answer(rationale: str = "", *, confidence: float = 0.0) -> Action:
    return Action(type="answer", rationale=rationale, completes_turn=True, confidence=confidence)


def read_artifact(
    filename: str,
    *,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    max_chars: Optional[int] = None,
    rationale: str = "",
) -> Action:
    params: dict[str, Any] = {"filename": filename}
    if start_line is not None:
        params["start_line"] = start_line
    if end_line is not None:
        params["end_line"] = end_line
    if max_chars is not None:
        params["max_chars"] = max_chars
    return Action(type="read_artifact", params=params, rationale=rationale)


def write_artifact(filename: str, content: str, *, rationale: str = "") -> Action:
    return Action(
        type="write_artifact",
        params={"filename": filename, "content": content},
        rationale=rationale,
        completes_turn=True,
    )


def edit_artifact(
    filename: str,
    *,
    old_text: Optional[str] = None,
    new_text: Optional[str] = None,
    edits: Optional[list[dict[str, Any]]] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    occurrence_index: Optional[int] = None,
    replace_all: bool = False,
    dry_run: bool = False,
    rationale: str = "",
) -> Action:
    params: dict[str, Any] = {"filename": filename}
    if edits is not None:
        params["edits"] = edits
    if old_text is not None:
        params["old_text"] = old_text
    if new_text is not None:
        params["new_text"] = new_text
    if start_line is not None:
        params["start_line"] = start_line
    if end_line is not None:
        params["end_line"] = end_line
    if occurrence_index is not None:
        params["occurrence_index"] = occurrence_index
    if replace_all:
        params["replace_all"] = True
    if dry_run:
        params["dry_run"] = True
    return Action(
        type="edit_artifact",
        params=params,
        rationale=rationale,
        completes_turn=True,
    )


def run_tool(name: str, params: dict[str, Any], *, rationale: str = "") -> Action:
    return Action(type="run_tool", params={"name": name, **params}, rationale=rationale)


def is_edit_applied(tool_result: dict[str, Any]) -> bool:
    """An edit is only 'done' if it actually changed the file.

    This is the honesty guarantee: a zero-replacement edit is NOT success, so
    the loop must surface it (clarify / retry) instead of silently converging.
    """
    if not isinstance(tool_result, dict):
        return False
    if str(tool_result.get("status")) != "ok":
        return False
    return int(tool_result.get("replacements") or 0) >= 1


__all__ = [
    "Action",
    "ActionType",
    "ACTION_TOOL_NAMES",
    "answer",
    "read_artifact",
    "write_artifact",
    "edit_artifact",
    "run_tool",
    "is_edit_applied",
]
