"""
Manuscript subsystem — session-scoped long-form writing (see docs/MANUSCRIPT_WRITING.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.config.settings import settings
from app.services.artifact_tools import list_task_artifacts, read_artifact_tail, task_artifact_dir

WRITING_TOOL_NAMES = frozenset({"write_text_artifact", "append_text_artifact"})

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
_OUTLINE_MARKERS = ("outline", "大纲", "提纲")


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
            "novel_filename": self.body_path,
            "outline_filename": self.outline_path,
            "novel_bytes": self.body_bytes,
            "files": self.files,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    @property
    def novel_filename(self) -> Optional[str]:
        return self.body_path

    @property
    def outline_filename(self) -> Optional[str]:
        return self.outline_path


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


def resolve_outline_filename(
    *,
    manuscript: Manuscript,
    payload: dict[str, Any],
    intent: Optional[dict[str, Any]] = None,
) -> str:
    """Bound outline path, else model/mission names, else default."""
    if manuscript.outline_path:
        return str(manuscript.outline_path)
    intent = intent or {}
    mission = payload.get("mission") or {}
    policy = mission.get("step_policy") if isinstance(mission.get("step_policy"), dict) else {}
    return _pick_artifact_basename(
        intent.get("outline_filename"),
        intent.get("outline_path"),
        payload.get("outline_filename"),
        policy.get("outline_artifact"),
        default=_default_outline(),
    )


def resolve_body_filename(
    *,
    manuscript: Manuscript,
    payload: dict[str, Any],
    intent: Optional[dict[str, Any]] = None,
) -> str:
    """Bound body path, else model/mission names, else default."""
    if manuscript.body_path:
        return str(manuscript.body_path)
    intent = intent or {}
    mission = payload.get("mission") or {}
    policy = mission.get("step_policy") if isinstance(mission.get("step_policy"), dict) else {}
    return _pick_artifact_basename(
        intent.get("body_filename"),
        intent.get("body_path"),
        intent.get("novel_filename"),
        payload.get("novel_filename"),
        payload.get("body_filename"),
        policy.get("body_artifact"),
        policy.get("artifact_path"),
        default=_default_body(),
    )


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
    if not out.get("outline_filename") and policy.get("outline_artifact"):
        out["outline_filename"] = str(policy["outline_artifact"]).strip()
    if not out.get("body_filename") and policy.get("body_artifact"):
        out["body_filename"] = str(policy["body_artifact"]).strip()
    for key in ("outline_filename", "body_filename"):
        if out.get(key):
            out[key] = sanitize_artifact_basename(out[key])
    return out


def apply_planner_artifact_names(
    payload: dict[str, Any],
    *,
    planning_result: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Lift planner-chosen basenames into mission.step_policy and payload."""
    out = dict(payload)
    manuscript = out.get("manuscript") or out.get("session_artifacts") or {}
    if manuscript.get("body_path") or manuscript.get("outline_path"):
        return sync_payload_artifact_names(out)

    plan_intent: dict[str, Any] = {}
    if isinstance(planning_result, dict):
        raw = planning_result.get("writing_intent")
        if isinstance(raw, dict):
            plan_intent = raw

    intent = dict(out.get("writing_intent") or plan_intent or {})
    mission = out.get("mission")
    if isinstance(mission, dict) and str(mission.get("kind") or "") == "writing":
        policy = dict(mission.get("step_policy") or {})
        body = _pick_artifact_basename(
            policy.get("body_artifact"),
            policy.get("artifact_path"),
            intent.get("body_filename"),
            intent.get("novel_filename"),
            plan_intent.get("body_filename"),
            out.get("novel_filename"),
            out.get("body_filename"),
            default=_default_body(),
        )
        outline = _pick_artifact_basename(
            policy.get("outline_artifact"),
            intent.get("outline_filename"),
            plan_intent.get("outline_filename"),
            out.get("outline_filename"),
            default=_default_outline(),
        )
        policy["body_artifact"] = body
        policy["outline_artifact"] = outline
        out["mission"] = {**mission, "step_policy": policy}

    return sync_payload_artifact_names(out, intent=intent)


def sync_payload_artifact_names(
    payload: dict[str, Any],
    *,
    intent: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Promote intent/mission artifact names onto payload before manuscript bind."""
    out = dict(payload)
    intent = dict(intent or out.get("writing_intent") or {})
    manuscript = out.get("manuscript") or out.get("session_artifacts") or {}
    mission = out.get("mission") or {}
    policy = mission.get("step_policy") if isinstance(mission.get("step_policy"), dict) else {}

    if not manuscript.get("outline_path"):
        outline_raw = (
            intent.get("outline_filename")
            or out.get("outline_filename")
            or policy.get("outline_artifact")
        )
        if outline_raw:
            out["outline_filename"] = sanitize_artifact_basename(outline_raw)

    if not manuscript.get("body_path"):
        body_raw = (
            intent.get("body_filename")
            or intent.get("novel_filename")
            or out.get("novel_filename")
            or out.get("body_filename")
            or policy.get("body_artifact")
            or policy.get("artifact_path")
        )
        if body_raw:
            out["novel_filename"] = sanitize_artifact_basename(body_raw)

    return out


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


def resolve_manuscript(task_id: str, stored: Optional[dict[str, Any]] = None) -> Manuscript:
    """Resolve canonical body/outline paths from state or artifact directory."""
    files = list_task_artifacts(task_id)
    ms = Manuscript(task_id=task_id, files=files)

    if stored:
        body = stored.get("body_path")
        outline = stored.get("outline_path")
        ms.chapter_cursor = int(stored.get("chapter_cursor") or 0)
        ms.last_chapter_index = int(stored.get("last_chapter_index") or 0)
        ms.revision = int(stored.get("revision") or 0)
        ms.outline_revision = int(stored.get("outline_revision") or 0)
        ms.body_revision = int(stored.get("body_revision") or 0)
        ms.body_outline_revision_seen = int(stored.get("body_outline_revision_seen") or 0)
        if body and any(f.get("filename") == body for f in files):
            ms.body_path = str(body)
            ms.body_bytes = int(stored.get("body_bytes") or 0)
        if outline and any(f.get("filename") == outline for f in files):
            ms.outline_path = str(outline)
            ms.outline_bytes = int(stored.get("outline_bytes") or 0)
        if ms.body_path:
            return _refresh_bytes(ms)

    outline_candidates: list[dict[str, Any]] = []
    body_candidates: list[dict[str, Any]] = []
    for item in files:
        name = str(item.get("filename") or "")
        if not name:
            continue
        if _is_outline_name(name):
            outline_candidates.append(item)
        else:
            body_candidates.append(item)

    if outline_candidates:
        outline_candidates.sort(key=lambda x: int(x.get("bytes") or 0), reverse=True)
        pick = outline_candidates[0]
        ms.outline_path = str(pick["filename"])
        ms.outline_bytes = int(pick.get("bytes") or 0)

    if body_candidates:
        default_body = _default_body()
        by_name = {str(f["filename"]): f for f in body_candidates}
        if default_body in by_name and int(by_name[default_body].get("bytes") or 0) >= 512:
            pick = by_name[default_body]
        else:
            body_candidates.sort(key=lambda x: int(x.get("bytes") or 0), reverse=True)
            pick = body_candidates[0]
        ms.body_path = str(pick["filename"])
        ms.body_bytes = int(pick.get("bytes") or 0)

    return ms


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
    from app.services.mission_schema import should_use_mission_runtime

    if (
        mission_block
        and should_use_mission_runtime({"mission": mission_block}, "")
        and not active_mission
        and int(mission_step or 0) < 1
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
    if not ms.files and not ms.body_path:
        return payload

    tail_chars = int(getattr(settings, "MANUSCRIPT_TAIL_EXCERPT_CHARS", 2400))
    enriched = {**payload, "manuscript": ms.to_dict(), "session_artifacts": ms.to_dict()}
    enriched = sync_payload_artifact_names(enriched, intent=payload.get("writing_intent"))

    if ms.body_path:
        enriched["novel_filename"] = ms.body_path
        tail = read_artifact_tail(task_id, ms.body_path, max_chars=tail_chars)
        if tail:
            enriched["previous_artifact_excerpt"] = tail
        enriched["previous_artifact_summary"] = f"{ms.body_path}（约 {ms.body_bytes} 字节）"

    if ms.outline_path:
        enriched["outline_filename"] = ms.outline_path

    if (session_turn > 1 or is_continue_writing_goal(goal)) and ms.body_path:
        enriched["longform_mode"] = True
        enriched["writing_instruction"] = (
            f"续写手稿正文「{ms.body_path}」；由 Writing 节点生成并 append；"
            f"勿在 tool_params 填写 content 或占位符。"
        )
    return enriched


def resolve_read_paths(state: dict[str, Any], filename: str) -> str:
    payload = state.get("input_payload") or {}
    manuscript = payload.get("manuscript") or payload.get("session_artifacts") or {}
    body = manuscript.get("body_path") or payload.get("novel_filename")
    outline = manuscript.get("outline_path") or payload.get("outline_filename")
    name = filename
    lower = name.lower()
    if lower in ("novel.txt", "body.txt") and body:
        return str(body)
    if lower in ("outline.txt",) and outline:
        return str(outline)
    return name
