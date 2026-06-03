"""WorkerExecutionPolicy — maps OMAW role capabilities to RAG/ReAct/tool budgets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.config.settings import settings


@dataclass
class RetrievalPolicy:
    required: bool = True
    domains: list[str] = field(default_factory=lambda: ["writing", "common"])
    must_include: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "domains": list(self.domains),
            "must_include": list(self.must_include),
        }


@dataclass
class ReactPolicy:
    enabled: bool = True
    max_steps: int = 3
    allowed_actions: list[str] = field(default_factory=list)
    exit_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_steps": self.max_steps,
            "allowed_actions": list(self.allowed_actions),
            "exit_paths": list(self.exit_paths),
        }


@dataclass
class ToolBudget:
    max_calls: int = 2
    allowed_tools: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_calls": self.max_calls,
            "allowed_tools": list(self.allowed_tools),
        }


@dataclass
class WorkerExecutionPolicy:
    agent: str
    capability: str
    retrieval: RetrievalPolicy = field(default_factory=RetrievalPolicy)
    react: ReactPolicy = field(default_factory=ReactPolicy)
    tool_budget: ToolBudget = field(default_factory=ToolBudget)
    may_write_artifacts: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "capability": self.capability,
            "retrieval": self.retrieval.to_dict(),
            "react": self.react.to_dict(),
            "tool_budget": self.tool_budget.to_dict(),
            "may_write_artifacts": self.may_write_artifacts,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> WorkerExecutionPolicy:
        if not isinstance(data, dict):
            return default_policy("writer", "write_chapter")
        ret_raw = data.get("retrieval") or {}
        react_raw = data.get("react") or {}
        tool_raw = data.get("tool_budget") or {}
        return cls(
            agent=str(data.get("agent") or ""),
            capability=str(data.get("capability") or ""),
            retrieval=RetrievalPolicy(
                required=bool(ret_raw.get("required", True)),
                domains=[str(d) for d in (ret_raw.get("domains") or ["writing", "common"])],
                must_include=[str(m) for m in (ret_raw.get("must_include") or [])],
            ),
            react=ReactPolicy(
                enabled=bool(react_raw.get("enabled", True)),
                max_steps=int(react_raw.get("max_steps") or 3),
                allowed_actions=[str(a) for a in (react_raw.get("allowed_actions") or [])],
                exit_paths=[str(e) for e in (react_raw.get("exit_paths") or [])],
            ),
            tool_budget=ToolBudget(
                max_calls=int(tool_raw.get("max_calls") or 2),
                allowed_tools=[str(t) for t in (tool_raw.get("allowed_tools") or [])],
            ),
            may_write_artifacts=bool(data.get("may_write_artifacts")),
        )


_CAPABILITY_MAP: dict[str, tuple[str, str]] = {
    "append_body": ("writer", "write_chapter"),
    "append_chapter": ("writer", "write_chapter"),
    "write_body": ("writer", "write_chapter"),
    "write_outline": ("writer", "write_outline"),
    "review_chapter": ("reviewer", "review_chapter"),
    "polish_chapter": ("editor", "polish_chapter"),
    "chapter_summary": ("writer", "write_chapter"),
    "consistency_check": ("continuity", "consistency_check"),
    "steer_replan": ("planner", "steer_replan"),
    "edit_plot": ("planner", "steer_replan"),
}


def resolve_agent_capability(work_item_kind: str) -> tuple[str, str]:
    key = (work_item_kind or "").strip().lower()
    return _CAPABILITY_MAP.get(key, ("writer", key or "write_chapter"))


def default_policy(agent: str, capability: str) -> WorkerExecutionPolicy:
    cfg = getattr(settings, "MISSION_OMA_WORKER_REACT", {}) or {}
    domains = list(
        getattr(settings, "MISSION_OMA_WORKER_RETRIEVAL_DOMAINS", None)
        or ["writing", "common"]
    )
    max_steps = int(cfg.get("default_max_steps") or 3)
    exit_paths = ["finish_with_answer", "reflection", "dead_letter"]

    if agent == "reviewer":
        actions = list(
            cfg.get("reviewer_allowed_actions")
            or ["retrieve_knowledge", "retrieve_memory", "reason", "finish"]
        )
        must_include = ["outline", "story_bible", "chapter_summary", "user_constraints"]
        may_write = True
    elif agent == "writer":
        actions = list(
            cfg.get("writer_allowed_actions")
            or ["retrieve_knowledge", "call_tool", "reason", "finish"]
        )
        must_include = ["outline", "story_bible", "chapter_summary", "user_constraints"]
        may_write = True
    elif agent == "editor":
        actions = list(
            cfg.get("editor_allowed_actions")
            or ["retrieve_knowledge", "reason", "finish"]
        )
        must_include = ["outline", "story_bible", "chapter_summary"]
        may_write = True
    elif agent == "planner":
        actions = list(
            cfg.get("planner_allowed_actions")
            or ["retrieve_knowledge", "retrieve_memory", "replan", "reason", "finish"]
        )
        must_include = ["outline", "user_constraints"]
        may_write = False
    elif agent == "continuity":
        actions = ["retrieve_knowledge", "reason", "finish"]
        must_include = ["story_bible", "chapter_summary"]
        may_write = False
    else:
        actions = ["retrieve_knowledge", "reason", "finish"]
        must_include = []
        may_write = False

    tool_budget = ToolBudget(max_calls=2, allowed_tools=["read_text_artifact", "grep_file"])
    if agent == "writer":
        tool_budget = ToolBudget(
            max_calls=2,
            allowed_tools=["read_text_artifact", "grep_file"],
        )

    return WorkerExecutionPolicy(
        agent=agent,
        capability=capability,
        retrieval=RetrievalPolicy(
            required=bool(getattr(settings, "MISSION_OMA_REQUIRE_FACT_BUNDLE", True)),
            domains=domains,
            must_include=must_include,
        ),
        react=ReactPolicy(
            enabled=bool(cfg.get("enabled", True)),
            max_steps=max_steps,
            allowed_actions=actions,
            exit_paths=exit_paths,
        ),
        tool_budget=tool_budget,
        may_write_artifacts=may_write,
    )


def policy_for_work_item(kind: str) -> WorkerExecutionPolicy:
    agent, cap = resolve_agent_capability(kind)
    return default_policy(agent, cap)
