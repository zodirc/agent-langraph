"""
Manuscript subsystem — session-scoped long-form writing (see docs/MANUSCRIPT_WRITING.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional

from app.config.settings import settings
from app.services.artifact_tools import (
    is_text_artifact_filename,
    list_task_artifacts,
    read_artifact_tail,
    task_artifact_dir,
)

WRITING_TOOL_NAMES = frozenset({"write_text_artifact", "append_text_artifact"})


def _coerce_dict(value: Any) -> dict[str, Any]:
    """Normalize payload fragments; ignore mistaken string scalars from clients."""
    return value if isinstance(value, dict) else {}


def sanitize_manuscript_bindings(raw: Any) -> dict[str, Any]:
    """
    Drop code/session filenames wrongly stored as manuscript text-artifact pointers.

    Engineering turns may leave main.cpp on disk; stale body_path causes
    read_text_artifact / enrich_payload to raise Extension not allowed.
    """
    ms = _coerce_dict(raw)
    if not ms:
        return {}
    out = dict(ms)
    for path_key, bytes_key in (
        ("body_path", "body_bytes"),
        ("outline_path", "outline_bytes"),
    ):
        path = out.get(path_key)
        if path and not is_manuscript_pointer_filename(str(path)):
            out.pop(path_key, None)
            if bytes_key:
                out.pop(bytes_key, None)
    return out


def _finalize_text_manuscript_pointers(ms: Manuscript) -> Manuscript:
    """Clear body/outline pointers that are not text-artifact extensions."""
    if ms.body_path and not is_manuscript_pointer_filename(ms.body_path):
        ms.body_path = None
        ms.body_bytes = 0
    if ms.outline_path and not is_manuscript_pointer_filename(ms.outline_path):
        ms.outline_path = None
        ms.outline_bytes = 0
    return ms


def _promote_text_artifact_basename(raw: Any) -> str | None:
    """
    Sanitize a basename for write_text_artifact / append_text_artifact only.

    Code and web assets (.cpp, .html, …) use engineering_mode session paths;
    return None instead of raising so planning can continue to route_audit.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return sanitize_artifact_basename(text)
    except ValueError:
        return None

_CONTINUE_GOAL_RE = re.compile(
    r"(续写|继续写|继续|追加|下一章|接着写|写下去|\bappend\b|\bcontinue\b)",
    re.IGNORECASE,
)
# Runtime tags from apply_writing_phase_from_decision: "[append_body] notes…"
_MISSION_PHASE_GOAL_TAG_RE = re.compile(
    r"\[(?:write_outline|append_body|write_body|append_chapter|reset_body|"
    r"polish_chapter|review_chapter|chapter_summary|arc_checkpoint|"
    r"consistency_check|bridge_chapter|patch_recent_chapter|reconcile_outline_body|"
    r"edit_plot)\][^[]*",
    re.IGNORECASE,
)
_MANUSCRIPT_POINTER_EXTENSIONS = frozenset({".txt", ".md"})
_OUTLINE_MARKERS = ("outline", "大纲", "提纲")


def is_manuscript_pointer_filename(filename: str) -> bool:
    """Body/outline artifact basenames (.txt/.md); excludes json/csv/log sidecars."""
    name = Path(str(filename or "")).name.strip()
    if not name:
        return False
    return Path(name).suffix.lower() in _MANUSCRIPT_POINTER_EXTENSIONS
_CHAPTER_FOOTER_RE = re.compile(r"（第\s*[^）]{1,12}章完）")
_CHAPTER_HEADER_MD_RE = re.compile(r"^#{1,3}\s*第\s*.+章", re.MULTILINE)
_BEAT_LINE_RE = re.compile(r"^(\s*[-*•]|\s*\d+[\.\)、]|【)")


@dataclass
class Manuscript:
    task_id: str
    body_path: Optional[str] = None
    outline_path: Optional[str] = None
    body_bytes: int = 0
    outline_bytes: int = 0
    chapter_cursor: int = 0
    last_chapter_index: int = 0
    revision: int = 0
    outline_revision: int = 0
    body_revision: int = 0
    body_outline_revision_seen: int = 0
    files: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "body_path": self.body_path,
            "outline_path": self.outline_path,
            "body_bytes": self.body_bytes,
            "outline_bytes": self.outline_bytes,
            "chapter_cursor": self.chapter_cursor,
            "last_chapter_index": self.last_chapter_index,
            "revision": self.revision,
            "outline_revision": self.outline_revision,
            "body_revision": self.body_revision,
            "body_outline_revision_seen": self.body_outline_revision_seen,
            "files": self.files,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


def _default_body() -> str:
    return str(getattr(settings, "MANUSCRIPT_DEFAULT_BODY", "novel.txt"))


def _default_outline() -> str:
    return str(getattr(settings, "MANUSCRIPT_DEFAULT_OUTLINE", "outline.txt"))


def sanitize_artifact_basename(raw: Any) -> str:
    """Validate a single-segment artifact basename for task-local writes."""
    from app.services.artifact_tools import _safe_filename

    name = str(raw or "").strip()
    if not name:
        raise ValueError("Artifact filename cannot be empty")
    return _safe_filename(name)


def _pick_artifact_basename(*candidates: Any, default: str) -> str:
    for raw in candidates:
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            return sanitize_artifact_basename(text)
        except ValueError:
            continue
    return sanitize_artifact_basename(default)


def normalize_writing_intent_filenames(
    intent: dict[str, Any],
    *,
    mission_block: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Sanitize optional filename fields on writing_intent; inherit mission step_policy."""
    out = dict(intent or {})
    policy = {}
    if isinstance(mission_block, dict):
        raw_policy = mission_block.get("step_policy")
        if isinstance(raw_policy, dict):
            policy = raw_policy
        else:
            policy = mission_block
    for key in ("body_filename",):
        if out.get(key):
            try:
                out[key] = sanitize_artifact_basename(out[key])
            except ValueError:
                out.pop(key, None)
    if policy.get("outline_artifact"):
        try:
            out.setdefault("outline_path_hint", sanitize_artifact_basename(policy["outline_artifact"]))
        except ValueError:
            pass
    if policy.get("body_artifact"):
        try:
            out.setdefault("body_path_hint", sanitize_artifact_basename(policy["body_artifact"]))
        except ValueError:
            pass
    return out


def _looks_like_body_in_outline(text: str) -> tuple[bool, str]:
    """Heuristic: reject full chapter prose saved as outline."""
    if _CHAPTER_FOOTER_RE.search(text):
        return True, "chapter footer in outline"
    headers = list(_CHAPTER_HEADER_MD_RE.finditer(text))
    if len(headers) == 1:
        chunk = text[headers[0].end() :].strip()
        if len(chunk) > 400:
            lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
            if len(lines) >= 5:
                beat_like = sum(1 for ln in lines if _BEAT_LINE_RE.match(ln))
                if beat_like / len(lines) < 0.2:
                    return True, "single-chapter narrative prose in outline"
    if text.count("」") >= 6 and text.count("「") >= 6:
        dialogue = re.findall(r"「[^」]{4,}」", text)
        if sum(len(m) for m in dialogue) > len(text) * 0.15:
            return True, "dialogue-heavy prose in outline"
    return False, ""


def _placeholder_patterns() -> tuple[str, ...]:
    raw = getattr(settings, "MANUSCRIPT_PLACEHOLDER_PATTERNS", None)
    if isinstance(raw, (list, tuple)) and raw:
        return tuple(str(p) for p in raw)
    return (
        "待续写",
        "待填充",
        "本回合内容",
        "占位",
        "请在本任务完成后",
        "由助手生成",
        "章节规划（示例）",
        "示例）",
    )


def strip_mission_system_goal_tags(goal: str) -> str:
    """Remove mission-injected [phase] segments before user-intent heuristics."""
    text = _MISSION_PHASE_GOAL_TAG_RE.sub(" ", goal or "")
    return re.sub(r"\s+", " ", text).strip()


def is_continue_writing_goal(goal: str) -> bool:
    text = strip_mission_system_goal_tags(goal)
    if not text:
        return False
    if _CONTINUE_GOAL_RE.search(text):
        return True
    return len(text) < 24 and any(k in text for k in ("续", "继续", "接着"))


def _is_outline_name(name: str) -> bool:
    return any(m in name.lower() or m in name for m in _OUTLINE_MARKERS)


def artifact_bytes_on_disk(task_id: str, filename: Optional[str]) -> int:
    """Bytes for a task artifact file (0 if missing). Prefer over stale state.body_bytes."""
    if not filename:
        return 0
    path = task_artifact_dir(task_id) / str(filename)
    if path.is_file():
        return int(path.stat().st_size)
    return 0


def manuscript_has_body(ms: Manuscript, *, min_bytes: int = 1) -> bool:
    """True when body_path exists and disk or state shows non-empty content."""
    if not ms.body_path:
        return False
    nbytes = max(int(ms.body_bytes or 0), artifact_bytes_on_disk(ms.task_id, ms.body_path))
    return nbytes >= min_bytes


def sync_manuscript_snapshot_atomic(state: "AgentState") -> "AgentState":
    """
    Refresh manuscript bytes from disk atomically after artifact commit.

    Ensures state/manuscript snapshot matches on-disk truth (checkpoint-first order).
    """
    from app.runtime.state import AgentState as _AgentState
    from app.services.manuscript_checkpoint import enrich_agent_state_manuscript

    return enrich_agent_state_manuscript(state)  # type: ignore[arg-type]


def resolve_manuscript(task_id: str, stored: Optional[dict[str, Any]] = None) -> Manuscript:
    """Resolve canonical body/outline paths from state or artifact directory."""
    files = list_task_artifacts(task_id)
    ms = Manuscript(task_id=task_id, files=files)

    if stored is not None and not isinstance(stored, dict):
        stored = None

    if stored:
        stored = sanitize_manuscript_bindings(stored)
        body = stored.get("body_path")
        outline = stored.get("outline_path")
        ms.chapter_cursor = int(stored.get("chapter_cursor") or 0)
        ms.last_chapter_index = int(stored.get("last_chapter_index") or 0)
        ms.revision = int(stored.get("revision") or 0)
        ms.outline_revision = int(stored.get("outline_revision") or 0)
        ms.body_revision = int(stored.get("body_revision") or 0)
        ms.body_outline_revision_seen = int(stored.get("body_outline_revision_seen") or 0)
        if body and is_manuscript_pointer_filename(str(body)) and any(
            f.get("filename") == body for f in files
        ):
            ms.body_path = str(body)
            ms.body_bytes = int(stored.get("body_bytes") or 0)
        if outline and is_manuscript_pointer_filename(str(outline)) and any(
            f.get("filename") == outline for f in files
        ):
            ms.outline_path = str(outline)
            ms.outline_bytes = int(stored.get("outline_bytes") or 0)
    from app.services.artifact_resolver import reconcile_manuscript_pointers

    ms = reconcile_manuscript_pointers(task_id, ms)
    return _finalize_text_manuscript_pointers(_refresh_bytes(ms))


def _sync_chapter_from_disk(ms: Manuscript) -> None:
    """Raise last_chapter_index / chapter_cursor when body on disk has newer chapter headers."""
    if not ms.body_path:
        return
    from app.services.manuscript_context import parse_last_chapter_index, read_body_text

    try:
        body_text = read_body_text(ms.task_id, ms.body_path)
    except OSError:
        return
    disk_last = parse_last_chapter_index(body_text)
    if disk_last <= 0:
        return
    ms.last_chapter_index = max(int(ms.last_chapter_index or 0), disk_last)
    ms.chapter_cursor = max(int(ms.chapter_cursor or 0), disk_last + 1)


def _refresh_bytes(ms: Manuscript) -> Manuscript:
    for item in ms.files:
        name = str(item.get("filename") or "")
        if name == ms.body_path:
            ms.body_bytes = int(item.get("bytes") or 0)
        if name == ms.outline_path:
            ms.outline_bytes = int(item.get("bytes") or 0)
    if ms.body_path:
        ms.body_bytes = max(
            int(ms.body_bytes or 0),
            artifact_bytes_on_disk(ms.task_id, ms.body_path),
        )
        _sync_chapter_from_disk(ms)
    if ms.outline_path:
        ms.outline_bytes = max(
            int(ms.outline_bytes or 0),
            artifact_bytes_on_disk(ms.task_id, ms.outline_path),
        )
    return ms


def validate_manuscript_content(
    content: str,
    *,
    action: str,
    min_chars: Optional[int] = None,
) -> tuple[bool, str]:
    text = (content or "").strip()
    if not text:
        return False, "empty content"

    for pattern in _placeholder_patterns():
        if pattern in text:
            return False, f"placeholder pattern: {pattern}"

    if action == "write_outline":
        floor = min_chars if min_chars is not None else int(
            getattr(settings, "MANUSCRIPT_MIN_OUTLINE_CHARS", 80)
        )
    else:
        floor = min_chars if min_chars is not None else int(
            getattr(settings, "MANUSCRIPT_MIN_BODY_CHARS", 200)
        )
    if len(text) < floor:
        return False, f"too short ({len(text)} < {floor})"

    if text.count("（待续写") >= 2 or text.count("【本回合") >= 1:
        return False, "template placeholder block"

    if action in ("write_outline", "rewrite_outline"):
        bad, reason = _looks_like_body_in_outline(text)
        if bad:
            return False, reason

    return True, "ok"


def build_writing_intent(
    *,
    goal: str,
    selected_tools: list[str],
    manuscript: Manuscript,
    session_turn: int,
    llm_intent: Optional[dict[str, Any]] = None,
    mission_block: Optional[dict[str, Any]] = None,
    active_mission: Optional[dict[str, Any]] = None,
    mission_step: int = 0,
) -> dict[str, Any]:
    """Derive writing_intent from plan tools + session (no file content)."""
    from app.services.writing_step import should_delegate_planning_writing_to_mission

    if should_delegate_planning_writing_to_mission(
        mission_block=mission_block,
        payload={"mission": mission_block} if mission_block else None,
    ):
        return {
            "enabled": False,
            "delegated_to": "mission",
            "source": "planning",
        }

    tools = set(selected_tools)
    writing_tools = tools & WRITING_TOOL_NAMES
    if not writing_tools and not (llm_intent or {}).get("enabled"):
        return {"enabled": False}

    intent: dict[str, Any] = dict(llm_intent or {})
    intent["enabled"] = True
    intent.setdefault("source", "planning")

    continue_turn = session_turn > 1 or is_continue_writing_goal(goal)
    has_body = bool(manuscript.body_path) and manuscript.body_bytes > 0

    action_aliases = {
        "write": "write_body",
        "draft": "write_body",
        "append": "append_body",
        "continue": "append_body",
        "outline": "write_outline",
        "polish": "polish_chapter",
        "rewrite": "polish_chapter",
        "review": "review_chapter",
        "summary": "chapter_summary",
    }
    raw_action = str(intent.get("action") or "").strip().lower()
    if raw_action:
        intent["action"] = action_aliases.get(raw_action, raw_action)

    if intent.get("action"):
        action = str(intent["action"])
    elif "append_text_artifact" in writing_tools or (continue_turn and has_body):
        action = "append_body"
    elif "write_text_artifact" in writing_tools and (
        "大纲" in goal or "outline" in goal.lower()
    ):
        action = "write_outline"
    elif "write_text_artifact" in writing_tools:
        action = "write_body" if not has_body else "append_body"
    else:
        action = "append_body" if has_body else "write_body"

    intent["action"] = action
    from app.services.artifact_content import parse_requested_chars
    from app.services.manuscript_context import parse_last_chapter_index, read_body_text

    if has_body and manuscript.body_path:
        body_text = read_body_text(manuscript.task_id, manuscript.body_path)
        last_ch = parse_last_chapter_index(body_text)
        if action in ("polish_chapter", "review_chapter", "chapter_summary"):
            intent.setdefault("chapter_index", max(1, last_ch))
        else:
            intent.setdefault("chapter_index", max(1, last_ch + 1))

    requested = parse_requested_chars(goal)
    if intent.get("target_chars") is None:
        intent["target_chars"] = requested or int(settings.ARTIFACT_CHUNK_CHARS)
    intent.setdefault(
        "min_chars",
        int(getattr(settings, "MANUSCRIPT_MIN_OUTLINE_CHARS", 80))
        if action == "write_outline"
        else int(getattr(settings, "MANUSCRIPT_MIN_BODY_CHARS", 200)),
    )
    if continue_turn and has_body and action == "write_body":
        intent["action"] = "append_body"

    intent = normalize_writing_intent_filenames(intent, mission_block=mission_block)
    return intent


def split_execution_tools(selected_tools: list[str]) -> tuple[list[str], list[str]]:
    """Return (non_writing_tools, writing_tools)."""
    writing = [t for t in selected_tools if t in WRITING_TOOL_NAMES]
    other = [t for t in selected_tools if t not in WRITING_TOOL_NAMES]
    return other, writing


def strip_writing_content_from_tool_params(tool_params: dict[str, Any]) -> dict[str, Any]:
    """Remove content fields from write/append — Writing node owns generation."""
    cleaned: dict[str, Any] = {}
    for name, cfg in tool_params.items():
        if name not in WRITING_TOOL_NAMES:
            cleaned[name] = cfg
            continue
        if isinstance(cfg, dict):
            slim = {k: v for k, v in cfg.items() if k != "content"}
            cleaned[name] = slim
        else:
            cleaned[name] = cfg
    return cleaned


def enrich_payload(
    payload: dict[str, Any],
    task_id: str,
    *,
    session_turn: int = 1,
    manuscript: Optional[Manuscript] = None,
) -> dict[str, Any]:
    goal = str(payload.get("goal") or payload.get("query") or "").strip()
    ms = manuscript or resolve_manuscript(task_id)
    if manuscript is not None:
        cleaned = sanitize_manuscript_bindings(manuscript.to_dict())
        if not cleaned.get("body_path") and not cleaned.get("outline_path") and not ms.files:
            return payload
        if cleaned.get("body_path") or cleaned.get("outline_path"):
            ms = Manuscript(task_id=task_id, files=ms.files, **{
                k: cleaned[k]
                for k in (
                    "body_path",
                    "outline_path",
                    "body_bytes",
                    "outline_bytes",
                    "chapter_cursor",
                    "last_chapter_index",
                    "revision",
                    "outline_revision",
                    "body_revision",
                    "body_outline_revision_seen",
                )
                if k in cleaned
            })
    elif not ms.files and not ms.body_path and not ms.outline_path:
        return payload

    from app.services.artifact_resolver import build_artifact_manifest

    tail_chars = int(getattr(settings, "MANUSCRIPT_TAIL_EXCERPT_CHARS", 2400))
    enriched = {**payload, "manuscript": ms.to_dict()}
    enriched["artifact_manifest"] = [
        e.to_dict() for e in build_artifact_manifest(task_id, manuscript=ms, payload=payload)
    ]

    if ms.body_path and is_text_artifact_filename(ms.body_path):
        tail = read_artifact_tail(task_id, ms.body_path, max_chars=tail_chars)
        if tail:
            enriched["previous_artifact_excerpt"] = tail
        enriched["previous_artifact_summary"] = f"{ms.body_path}（约 {ms.body_bytes} 字节）"

    if (
        (session_turn > 1 or is_continue_writing_goal(goal))
        and ms.body_path
        and is_text_artifact_filename(ms.body_path)
    ):
        enriched["longform_mode"] = True
        enriched["writing_instruction"] = (
            f"续写手稿正文「{ms.body_path}」；由 Writing 节点生成并 append；"
            f"勿在 tool_params 填写 content 或占位符。"
        )
    return enriched
