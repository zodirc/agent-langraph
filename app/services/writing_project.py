"""Writing project manifest — single novel body file + chapter progress in project.json."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.artifact_tools import task_artifact_dir

DEFAULT_OUTLINE = "大纲.md"
DEFAULT_BIBLE = "素材卡.md"
DEFAULT_CHAPTERS_DIR = "正文"
DEFAULT_CHAPTER_PATTERN = "第{n:03d}章.md"
DEFAULT_BODY_FILE = "正文/novel.md"
DEFAULT_BODY_MODE = "single"
DEFAULT_WORDS_PER_CHAPTER = 4000
MIN_WORDS_PER_CHAPTER = 4000
CHAPTER_COMPLETION_RATIO = 0.9
MAX_CHAPTER_CONTINUATION_SEGMENTS = 4
_PROJECT_FILENAME = "project.json"
_OUTLINE_HINT_RE = re.compile(r"(?i)(大纲|outline)")
_CHARS_RE = re.compile(r"(\d+)\s*字")
_WAN_RE = re.compile(r"([一二两三四五六七八九十\d]+)\s*万\s*字?")
_CHAPTER_DONE_RE = re.compile(r"（第(\d+)章完）")
_PER_CHAPTER_FILE_RE = re.compile(r"第(\d+)章\.md$", re.IGNORECASE)
_OUTLINE_CHAPTER_HEAD_RE = re.compile(
    r"(?im)^(?:#+\s*)?(?:第\s*([0-9]+|[一二三四五六七八九十百千零两]+)\s*章|chapter\s*(\d+))[^\n]*"
)
_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


@dataclass
class WritingProject:
    kind: str = "novel"
    outline: str = DEFAULT_OUTLINE
    bible: str = DEFAULT_BIBLE
    body_mode: str = DEFAULT_BODY_MODE
    body_file: str = DEFAULT_BODY_FILE
    chapters_dir: str = DEFAULT_CHAPTERS_DIR
    chapter_pattern: str = DEFAULT_CHAPTER_PATTERN
    next_chapter: int = 1
    target_chapter: int = 0
    words_per_chapter: int = DEFAULT_WORDS_PER_CHAPTER
    created_at: str = ""
    current_chapter_incomplete: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WritingProject:
        body_mode = str(data.get("body_mode") or DEFAULT_BODY_MODE)
        body_file = str(data.get("body_file") or DEFAULT_BODY_FILE)
        if body_mode != "single" and not data.get("body_mode"):
            body_mode = DEFAULT_BODY_MODE
            body_file = DEFAULT_BODY_FILE
        words = max(
            MIN_WORDS_PER_CHAPTER,
            int(data.get("words_per_chapter") or DEFAULT_WORDS_PER_CHAPTER),
        )
        return cls(
            kind=str(data.get("kind") or "novel"),
            outline=str(data.get("outline") or DEFAULT_OUTLINE),
            bible=str(data.get("bible") or DEFAULT_BIBLE),
            body_mode=body_mode,
            body_file=body_file,
            chapters_dir=str(data.get("chapters_dir") or DEFAULT_CHAPTERS_DIR),
            chapter_pattern=str(data.get("chapter_pattern") or DEFAULT_CHAPTER_PATTERN),
            next_chapter=max(1, int(data.get("next_chapter") or 1)),
            target_chapter=max(0, int(data.get("target_chapter") or 0)),
            words_per_chapter=words,
            created_at=str(data.get("created_at") or ""),
            current_chapter_incomplete=bool(data.get("current_chapter_incomplete")),
        )


def project_json_path(task_id: str) -> Path:
    return task_artifact_dir(task_id) / _PROJECT_FILENAME


def writing_project_manifest_exists(task_id: str) -> bool:
    return project_json_path(task_id).is_file()


def load_project(task_id: str) -> WritingProject | None:
    path = project_json_path(task_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return WritingProject.from_dict(data)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return None


def save_project(task_id: str, project: WritingProject) -> None:
    path = project_json_path(task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(project.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def completion_threshold(project: WritingProject) -> int:
    return int(project.words_per_chapter * CHAPTER_COMPLETION_RATIO)


def parse_words_per_chapter_from_goal(goal: str) -> int | None:
    text = (goal or "").strip()
    if not text:
        return None
    if "一万" in text or "1万" in text:
        return max(10000, MIN_WORDS_PER_CHAPTER)
    wan = _WAN_RE.search(text)
    if wan:
        raw = wan.group(1)
        if raw.isdigit():
            return max(int(raw) * 10000, MIN_WORDS_PER_CHAPTER)
        if raw in ("一", "1"):
            return max(10000, MIN_WORDS_PER_CHAPTER)
        if raw == "两":
            return max(20000, MIN_WORDS_PER_CHAPTER)
    match = _CHARS_RE.search(text)
    if match:
        return max(int(match.group(1)), MIN_WORDS_PER_CHAPTER)
    return None


def parse_target_chapter_from_goal(goal: str) -> int | None:
    text = (goal or "").strip()
    if not text:
        return None
    range_match = re.search(r"(\d+)\s*[-~到至]\s*(\d+)\s*章", text)
    if range_match:
        return max(int(range_match.group(1)), int(range_match.group(2)))
    for pattern in (
        r"写到第?(\d+)章",
        r"写(?:满|完|好)?第?(\d+)章",
        r"共(\d+)章",
        r"连续写(\d+)章",
    ):
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return None


def apply_goal_to_project(project: WritingProject, goal: str) -> WritingProject:
    changed = False
    words = parse_words_per_chapter_from_goal(goal)
    if words and words != project.words_per_chapter:
        project.words_per_chapter = words
        changed = True
    target = parse_target_chapter_from_goal(goal)
    if target and target != project.target_chapter:
        project.target_chapter = max(project.target_chapter, target)
        changed = True
    return project if changed else project


def chapter_relative_path(project: WritingProject, chapter_num: int) -> str:
    name = project.chapter_pattern.format(n=chapter_num)
    return f"{project.chapters_dir}/{name}"


def chapter_absolute_path(task_id: str, project: WritingProject, chapter_num: int) -> Path:
    return task_artifact_dir(task_id) / chapter_relative_path(project, chapter_num)


def body_absolute_path(task_id: str, project: WritingProject) -> Path:
    return task_artifact_dir(task_id) / project.body_file


def _list_artifact_files(task_id: str) -> list[Path]:
    root = task_artifact_dir(task_id)
    if not root.is_dir():
        return []
    return [p for p in root.rglob("*") if p.is_file() and p.name != _PROJECT_FILENAME]


def _find_legacy_outline(task_id: str) -> str | None:
    for path in _list_artifact_files(task_id):
        rel = path.relative_to(task_artifact_dir(task_id)).as_posix()
        if _OUTLINE_HINT_RE.search(rel):
            return rel
    return None


def _find_legacy_body(task_id: str) -> str | None:
    preferred = ("正文/novel.md", "novel.txt", "body.txt", "正文.txt", "小说.txt")
    manifest_names = {p.relative_to(task_artifact_dir(task_id)).as_posix() for p in _list_artifact_files(task_id)}
    for name in preferred:
        if name in manifest_names:
            return name
    for name in sorted(manifest_names):
        if not _OUTLINE_HINT_RE.search(name) and not name.endswith("素材卡.md"):
            return name
    return None


def _merge_per_chapter_files(task_id: str, project: WritingProject) -> str:
    root = task_artifact_dir(task_id)
    chapters_dir = root / project.chapters_dir
    if not chapters_dir.is_dir():
        return ""
    chapter_files: list[tuple[int, Path]] = []
    for path in chapters_dir.iterdir():
        if not path.is_file():
            continue
        match = _PER_CHAPTER_FILE_RE.search(path.name)
        if match:
            chapter_files.append((int(match.group(1)), path))
    if not chapter_files:
        return ""
    chapter_files.sort(key=lambda item: item[0])
    parts: list[str] = []
    for num, path in chapter_files:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        if not _CHAPTER_DONE_RE.search(text):
            text = f"{text}\n\n（第{num}章完）"
        parts.append(text)
    return "\n\n".join(parts)


def migrate_legacy_layout(task_id: str) -> WritingProject | None:
    """Map existing outline/body files into project.json when possible."""
    outline_rel = _find_legacy_outline(task_id)
    body_rel = _find_legacy_body(task_id)
    per_chapter = _merge_per_chapter_files(task_id, WritingProject())
    if not outline_rel and not body_rel and not per_chapter:
        return None

    root = task_artifact_dir(task_id)
    project = WritingProject(
        outline=outline_rel or DEFAULT_OUTLINE,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    body_path = body_absolute_path(task_id, project)
    body_path.parent.mkdir(parents=True, exist_ok=True)

    merged_parts: list[str] = []
    if body_rel and body_rel != outline_rel:
        src = root / body_rel
        if src.is_file() and src.stat().st_size > 0:
            merged_parts.append(src.read_text(encoding="utf-8").strip())
    if per_chapter:
        merged_parts.append(per_chapter)

    if merged_parts and not body_path.exists():
        combined = "\n\n".join(part for part in merged_parts if part)
        body_path.write_text(combined, encoding="utf-8")
        completed = len(list(_CHAPTER_DONE_RE.finditer(combined)))
        project.next_chapter = max(1, completed + 1)

    if outline_rel and not (root / project.outline).exists() and (root / outline_rel).exists():
        if outline_rel != project.outline:
            project.outline = outline_rel

    bible_path = root / project.bible
    if not bible_path.exists():
        bible_path.write_text(
            "# 素材卡（自动生成，可手动编辑）\n\n",
            encoding="utf-8",
        )

    save_project(task_id, project)
    return project


def ensure_writing_project(task_id: str, *, goal: str = "") -> WritingProject:
    existing = load_project(task_id)
    if existing:
        if goal:
            updated = apply_goal_to_project(existing, goal)
            save_project(task_id, updated)
        return load_project(task_id) or existing
    migrated = migrate_legacy_layout(task_id)
    if migrated:
        if goal:
            updated = apply_goal_to_project(migrated, goal)
            save_project(task_id, updated)
            return updated
        return migrated

    root = task_artifact_dir(task_id)
    words = parse_words_per_chapter_from_goal(goal) or DEFAULT_WORDS_PER_CHAPTER
    target = parse_target_chapter_from_goal(goal) or 0
    project = WritingProject(
        words_per_chapter=words,
        target_chapter=target,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    outline_path = root / project.outline
    if not outline_path.exists():
        outline_path.write_text(
            "# 大纲\n\n## 第一章\n\n",
            encoding="utf-8",
        )
    bible_path = root / project.bible
    if not bible_path.exists():
        bible_path.write_text(
            "# 素材卡（自动生成，可手动编辑）\n\n",
            encoding="utf-8",
        )
    body_path = body_absolute_path(task_id, project)
    body_path.parent.mkdir(parents=True, exist_ok=True)
    save_project(task_id, project)
    return project


def _legacy_resolve_body_filename(task_id: str, goal: str) -> str:
    from app.services.artifact_edit_intent import resolve_artifact_edit_filename

    resolved = resolve_artifact_edit_filename(task_id, goal)
    if resolved:
        return resolved
    from app.services.artifact_resolver import build_artifact_manifest

    manifest = build_artifact_manifest(task_id)
    for preferred in ("正文/novel.md", "novel.txt", "body.txt", "正文.txt", "小说.txt"):
        for entry in manifest:
            if entry.filename.lower() == preferred.lower():
                return entry.filename
    for entry in manifest:
        name = entry.filename.lower()
        if "outline" not in name and "大纲" not in name:
            return entry.filename
    return ""


def _legacy_resolve_outline_filename(task_id: str) -> str:
    from app.services.artifact_resolver import build_artifact_manifest

    manifest = build_artifact_manifest(task_id)
    for entry in manifest:
        name = entry.filename.lower()
        if "outline" in name or "大纲" in name:
            return entry.filename
    return ""


def resolve_body_target(
    task_id: str,
    goal: str,
    *,
    operator: str = "",
) -> str:
    project = load_project(task_id)
    if project:
        return project.body_file

    return _legacy_resolve_body_filename(task_id, goal)


def resolve_outline_target(task_id: str, goal: str = "") -> str:
    project = load_project(task_id)
    if project:
        return project.outline
    return _legacy_resolve_outline_filename(task_id)


def outline_needs_kickoff(task_id: str) -> bool:
    """True when outline is missing or still only the auto-created stub."""
    rel = resolve_outline_target(task_id, "")
    if not rel:
        return True
    path = task_artifact_dir(task_id) / rel
    if not path.is_file():
        return True
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return True
    if not text:
        return True
    prose = "".join(
        ln.strip()
        for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    )
    if len(prose) >= 80:
        return False
    return len(text) < 400


def expected_body_target(task_id: str, operator: str) -> str | None:
    if operator not in ("append", "kickoff_body", "rewrite", "polish", "character"):
        return None
    return resolve_body_target(task_id, "", operator=operator)


def read_body_text(task_id: str, rel_path: str) -> str:
    path = task_artifact_dir(task_id) / rel_path
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def read_outline_text(task_id: str) -> str:
    project = load_project(task_id)
    if not project:
        return ""
    path = task_artifact_dir(task_id) / project.outline
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _parse_outline_heading_chapter(line: str) -> int | None:
    match = _OUTLINE_CHAPTER_HEAD_RE.match(line.strip())
    if not match:
        return None
    if match.group(2):
        return int(match.group(2))
    raw = str(match.group(1) or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    if len(raw) == 1 and raw in _CN_DIGIT:
        return _CN_DIGIT[raw]
    if raw == "十":
        return 10
    if raw.startswith("十") and len(raw) == 2 and raw[1] in _CN_DIGIT:
        return 10 + _CN_DIGIT[raw[1]]
    if raw.endswith("十") and len(raw) == 2 and raw[0] in _CN_DIGIT:
        return _CN_DIGIT[raw[0]] * 10
    if "十" in raw:
        left, _, right = raw.partition("十")
        tens = _CN_DIGIT.get(left, 1) if left else 1
        ones = _CN_DIGIT.get(right, 0) if right else 0
        return tens * 10 + ones
    return None


def extract_outline_for_chapter(task_id: str, chapter: int, *, max_chars: int = 3000) -> str:
    """Return the outline section for chapter N (plot beats), when headings are present."""
    if chapter < 1:
        return ""
    text = read_outline_text(task_id)
    if not text.strip():
        return ""
    lines = text.splitlines()
    sections: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        num = _parse_outline_heading_chapter(line)
        if num is not None:
            sections.append((num, index))
    if not sections:
        excerpt = text.strip()
        return excerpt[:max_chars] + ("\n...(truncated)" if len(excerpt) > max_chars else "")
    start_line = 0
    for num, line_idx in sections:
        if num == chapter:
            start_line = line_idx
            break
    else:
        return ""
    end_line = len(lines)
    for num, line_idx in sections:
        if line_idx > start_line and num > chapter:
            end_line = line_idx
            break
    excerpt = "\n".join(lines[start_line:end_line]).strip()
    if len(excerpt) > max_chars:
        return excerpt[:max_chars] + "\n...(truncated)"
    return excerpt


def chapter_prefix_end(full_text: str) -> int:
    """Byte index in full_text after the last completed-chapter footer."""
    last_end = 0
    for match in _CHAPTER_DONE_RE.finditer(full_text):
        last_end = match.end()
    return last_end


def merge_body_write_content(full_text: str, new_chapter_text: str) -> str:
    """Preserve completed chapters when write_text_artifact targets the novel body."""
    new_chapter_text = new_chapter_text.strip()
    if not full_text.strip():
        return new_chapter_text
    prefix_end = chapter_prefix_end(full_text)
    prefix = full_text[:prefix_end].rstrip()
    prior_current = full_text[prefix_end:].strip()
    if prior_current and new_chapter_text:
        merged_current = f"{prior_current}\n\n{new_chapter_text}"
    elif new_chapter_text:
        merged_current = new_chapter_text
    else:
        merged_current = prior_current
    if prefix:
        return f"{prefix}\n\n{merged_current}".strip()
    return merged_current


def novel_tail_excerpt(task_id: str, *, max_chars: int = 1200) -> str:
    """Tail of the in-progress chapter for continuation prompts."""
    project = load_project(task_id)
    if not project:
        return ""
    text = read_body_text(task_id, project.body_file)
    tail = slice_current_chapter_text(text) or text
    tail = tail.strip()
    if len(tail) <= max_chars:
        return tail
    return tail[-max_chars:]


def slice_current_chapter_text(full_text: str) -> str:
    """Return prose for the in-progress chapter (text after the last chapter footer)."""
    if not full_text.strip():
        return ""
    last_end = 0
    for match in _CHAPTER_DONE_RE.finditer(full_text):
        last_end = match.end()
    return full_text[last_end:].strip()


def current_chapter_char_count(task_id: str) -> int:
    project = load_project(task_id)
    if not project:
        return 0
    text = read_body_text(task_id, project.body_file)
    return len(slice_current_chapter_text(text))


def chapter_char_count(task_id: str, rel_path: str) -> int:
    """Character count for the active chapter section (single-file mode)."""
    project = load_project(task_id)
    if project and rel_path.replace("\\", "/") == project.body_file.replace("\\", "/"):
        return current_chapter_char_count(task_id)
    return len(read_body_text(task_id, rel_path))


def is_body_path(task_id: str, filename: str) -> bool:
    project = load_project(task_id)
    if not project:
        return False
    norm = filename.replace("\\", "/")
    return norm == project.body_file.replace("\\", "/")


def is_chapter_path(task_id: str, filename: str) -> bool:
    """Alias for body path checks (single novel file)."""
    return is_body_path(task_id, filename)


def body_file_exists_nonempty(task_id: str) -> bool:
    project = load_project(task_id)
    if not project:
        return False
    path = body_absolute_path(task_id, project)
    return path.is_file() and path.stat().st_size > 0


def kickoff_should_write_not_append(task_id: str) -> bool:
    project = load_project(task_id)
    if not project:
        return True
    if project.current_chapter_incomplete:
        return False
    if project.next_chapter > 1:
        return False
    return not body_file_exists_nonempty(task_id)


def body_draft_tool_name(task_id: str, filename: str) -> str:
    """Return append_text_artifact for body continuation; write only for empty first chapter."""
    if not is_body_path(task_id, filename):
        return "write_text_artifact"
    if kickoff_should_write_not_append(task_id):
        return "write_text_artifact"
    return "append_text_artifact"


def batch_has_remaining(project: WritingProject) -> bool:
    if project.target_chapter <= 0:
        return False
    if project.current_chapter_incomplete:
        return True
    return project.next_chapter <= project.target_chapter


def post_chapter_write_update(
    task_id: str,
    filename: str,
    *,
    char_count: int | None = None,
) -> None:
    project = load_project(task_id)
    if not project or not is_body_path(task_id, filename):
        return
    norm = filename.replace("\\", "/")
    if norm != project.body_file.replace("\\", "/"):
        return

    count = current_chapter_char_count(task_id)
    if char_count is not None and count == 0:
        count = char_count
    threshold = completion_threshold(project)
    if count >= threshold:
        project.current_chapter_incomplete = False
        project.next_chapter += 1
    else:
        project.current_chapter_incomplete = True
    save_project(task_id, project)


def mark_chapter_continuation_needed(task_id: str, filename: str) -> None:
    project = load_project(task_id)
    if not project or not is_body_path(task_id, filename):
        return
    project.current_chapter_incomplete = True
    save_project(task_id, project)
