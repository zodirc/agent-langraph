"""Writing project manifest and fixed per-chapter file layout."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.services.artifact_tools import task_artifact_dir

DEFAULT_OUTLINE = "大纲.md"
DEFAULT_BIBLE = "素材卡.md"
DEFAULT_CHAPTERS_DIR = "正文"
DEFAULT_CHAPTER_PATTERN = "第{n:03d}章.md"
DEFAULT_WORDS_PER_CHAPTER = 3000
_PROJECT_FILENAME = "project.json"
_OUTLINE_HINT_RE = re.compile(r"(?i)(大纲|outline)")
_CHARS_RE = re.compile(r"(\d+)\s*字")
_WAN_RE = re.compile(r"([一二两三四五六七八九十\d]+)\s*万\s*字?")


@dataclass
class WritingProject:
    kind: str = "novel"
    outline: str = DEFAULT_OUTLINE
    bible: str = DEFAULT_BIBLE
    chapters_dir: str = DEFAULT_CHAPTERS_DIR
    chapter_pattern: str = DEFAULT_CHAPTER_PATTERN
    next_chapter: int = 1
    words_per_chapter: int = DEFAULT_WORDS_PER_CHAPTER
    created_at: str = ""
    current_chapter_incomplete: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WritingProject:
        return cls(
            kind=str(data.get("kind") or "novel"),
            outline=str(data.get("outline") or DEFAULT_OUTLINE),
            bible=str(data.get("bible") or DEFAULT_BIBLE),
            chapters_dir=str(data.get("chapters_dir") or DEFAULT_CHAPTERS_DIR),
            chapter_pattern=str(data.get("chapter_pattern") or DEFAULT_CHAPTER_PATTERN),
            next_chapter=max(1, int(data.get("next_chapter") or 1)),
            words_per_chapter=max(500, int(data.get("words_per_chapter") or DEFAULT_WORDS_PER_CHAPTER)),
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


def parse_words_per_chapter_from_goal(goal: str) -> int | None:
    text = (goal or "").strip()
    if not text:
        return None
    if "一万" in text or "1万" in text:
        return 10000
    wan = _WAN_RE.search(text)
    if wan:
        raw = wan.group(1)
        if raw.isdigit():
            return int(raw) * 10000
        if raw in ("一", "1"):
            return 10000
        if raw == "两":
            return 20000
    match = _CHARS_RE.search(text)
    if match:
        return int(match.group(1))
    return None


def chapter_relative_path(project: WritingProject, chapter_num: int) -> str:
    name = project.chapter_pattern.format(n=chapter_num)
    return f"{project.chapters_dir}/{name}"


def chapter_absolute_path(task_id: str, project: WritingProject, chapter_num: int) -> Path:
    return task_artifact_dir(task_id) / chapter_relative_path(project, chapter_num)


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
    preferred = ("novel.txt", "body.txt", "正文.txt", "小说.txt")
    manifest_names = {p.relative_to(task_artifact_dir(task_id)).as_posix() for p in _list_artifact_files(task_id)}
    for name in preferred:
        if name in manifest_names:
            return name
    for name in sorted(manifest_names):
        if not _OUTLINE_HINT_RE.search(name):
            return name
    return None


def migrate_legacy_layout(task_id: str) -> WritingProject | None:
    """Map existing outline/body files into project.json when possible."""
    outline_rel = _find_legacy_outline(task_id)
    body_rel = _find_legacy_body(task_id)
    if not outline_rel and not body_rel:
        return None

    root = task_artifact_dir(task_id)
    project = WritingProject(
        outline=outline_rel or DEFAULT_OUTLINE,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    chapters_dir = root / project.chapters_dir
    chapters_dir.mkdir(parents=True, exist_ok=True)

    if body_rel and body_rel != outline_rel:
        src = root / body_rel
        if src.is_file() and src.stat().st_size > 0:
            dest = chapter_absolute_path(task_id, project, 1)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            project.next_chapter = 2

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
        return existing
    migrated = migrate_legacy_layout(task_id)
    if migrated:
        return migrated

    root = task_artifact_dir(task_id)
    words = parse_words_per_chapter_from_goal(goal) or DEFAULT_WORDS_PER_CHAPTER
    project = WritingProject(
        words_per_chapter=words,
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
    (root / project.chapters_dir).mkdir(parents=True, exist_ok=True)
    save_project(task_id, project)
    return project


def _legacy_resolve_body_filename(task_id: str, goal: str) -> str:
    from app.services.artifact_edit_intent import resolve_artifact_edit_filename

    resolved = resolve_artifact_edit_filename(task_id, goal)
    if resolved:
        return resolved
    from app.services.artifact_resolver import build_artifact_manifest

    manifest = build_artifact_manifest(task_id)
    for preferred in ("novel.txt", "body.txt", "正文.txt", "小说.txt"):
        for entry in manifest:
            if entry.filename.lower() == preferred:
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
        if operator in ("rewrite", "polish", "character"):
            chapter_num = project.next_chapter
            if not project.current_chapter_incomplete and chapter_num > 1:
                chapter_num -= 1
            return chapter_relative_path(project, max(1, chapter_num))
        return chapter_relative_path(project, project.next_chapter)

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


def read_chapter_text(task_id: str, rel_path: str) -> str:
    path = task_artifact_dir(task_id) / rel_path
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def chapter_char_count(task_id: str, rel_path: str) -> int:
    return len(read_chapter_text(task_id, rel_path))


def is_chapter_path(task_id: str, filename: str) -> bool:
    project = load_project(task_id)
    if not project:
        return False
    norm = filename.replace("\\", "/")
    return norm.startswith(f"{project.chapters_dir}/")


def post_chapter_write_update(
    task_id: str,
    filename: str,
    *,
    char_count: int | None = None,
) -> None:
    project = load_project(task_id)
    if not project or not is_chapter_path(task_id, filename):
        return
    norm = filename.replace("\\", "/")
    target = chapter_relative_path(project, project.next_chapter)
    if norm != target:
        return

    count = char_count if char_count is not None else chapter_char_count(task_id, norm)
    threshold = int(project.words_per_chapter * 0.7)
    if count >= threshold:
        project.current_chapter_incomplete = False
        project.next_chapter += 1
    else:
        project.current_chapter_incomplete = True
    save_project(task_id, project)


def mark_chapter_continuation_needed(task_id: str, filename: str) -> None:
    project = load_project(task_id)
    if not project or not is_chapter_path(task_id, filename):
        return
    project.current_chapter_incomplete = True
    save_project(task_id, project)
