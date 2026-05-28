"""Structured schemas for long-form writing memory (Story Bible, events, quality)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional
from uuid import uuid4

EntryCategory = Literal["character", "location", "faction", "item", "rule", "event"]
EntryStatus = Literal["active", "archived", "pending"]
BodyAction = Literal[
    "keep_append",
    "append_with_bridge",
    "patch_recent_chapters",
    "rewrite_body",
]
ChangeLevel = Literal["minor", "moderate", "major"]
DiffSeverity = Literal["trivial", "minor", "moderate", "major"]
PatchType = Literal["rewrite_ending", "insert_scene", "modify_dialogue", "full_rewrite"]
EventType = Literal[
    "character_introduced",
    "character_state_changed",
    "relationship_changed",
    "plot_point_advanced",
    "foreshadow_planted",
    "foreshadow_resolved",
    "conflict_escalated",
    "conflict_resolved",
    "setting_established",
    "timeline_advanced",
]


@dataclass
class StyleContract:
    tone: str = ""
    pov: str = ""
    constraints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> StyleContract:
        if not isinstance(data, dict):
            return cls()
        return cls(
            tone=str(data.get("tone") or ""),
            pov=str(data.get("pov") or ""),
            constraints=[str(x) for x in (data.get("constraints") or []) if str(x).strip()],
        )


@dataclass
class OpenLoop:
    loop_id: str
    description: str
    planted_chapter: int = 0
    status: Literal["open", "resolved"] = "open"
    priority: int = 500

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OpenLoop:
        return cls(
            loop_id=str(data.get("loop_id") or uuid4().hex[:12]),
            description=str(data.get("description") or ""),
            planted_chapter=int(data.get("planted_chapter") or 0),
            status=data.get("status") or "open",  # type: ignore[assignment]
            priority=int(data.get("priority") or 500),
        )


@dataclass
class TimelineEvent:
    chapter_index: int
    label: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TimelineEvent:
        return cls(
            chapter_index=int(data.get("chapter_index") or 0),
            label=str(data.get("label") or ""),
            detail=str(data.get("detail") or ""),
        )


@dataclass
class StoryBibleEntry:
    key: str
    category: EntryCategory
    display_name: str
    content: str
    activation_keywords: list[str] = field(default_factory=list)
    priority: int = 500
    chapter_introduced: int = 0
    last_updated_chapter: int = 0
    status: EntryStatus = "active"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StoryBibleEntry:
        cat = str(data.get("category") or "character")
        if cat not in ("character", "location", "faction", "item", "rule", "event"):
            cat = "character"
        st = str(data.get("status") or "active")
        if st not in ("active", "archived", "pending"):
            st = "active"
        return cls(
            key=str(data.get("key") or ""),
            category=cat,  # type: ignore[assignment]
            display_name=str(data.get("display_name") or data.get("name") or ""),
            content=str(data.get("content") or ""),
            activation_keywords=[str(k) for k in (data.get("activation_keywords") or [])],
            priority=int(data.get("priority") or 500),
            chapter_introduced=int(data.get("chapter_introduced") or 0),
            last_updated_chapter=int(data.get("last_updated_chapter") or 0),
            status=st,  # type: ignore[assignment]
        )


@dataclass
class StoryBible:
    entries: dict[str, StoryBibleEntry] = field(default_factory=dict)
    open_loops: list[OpenLoop] = field(default_factory=list)
    timeline: list[TimelineEvent] = field(default_factory=list)
    style_contract: StyleContract = field(default_factory=StyleContract)
    version: int = 0
    updated_at: str = ""
    chapters: dict[str, Any] = field(default_factory=dict)
    characters: dict[str, Any] = field(default_factory=dict)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": {k: v.to_dict() for k, v in self.entries.items()},
            "open_loops": [o.to_dict() for o in self.open_loops],
            "timeline": [t.to_dict() for t in self.timeline],
            "style_contract": self.style_contract.to_dict(),
            "version": self.version,
            "updated_at": self.updated_at,
            "chapters": self.chapters,
            "characters": self.characters,
            "checkpoints": self.checkpoints,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> StoryBible:
        if not isinstance(data, dict):
            return cls()
        raw_entries = data.get("entries") or {}
        entries: dict[str, StoryBibleEntry] = {}
        if isinstance(raw_entries, dict):
            for k, v in raw_entries.items():
                if isinstance(v, dict):
                    entries[str(k)] = StoryBibleEntry.from_dict(v)
        legacy_chars = data.get("characters") or {}
        if isinstance(legacy_chars, dict) and not entries:
            for name, info in legacy_chars.items():
                if not isinstance(info, dict):
                    continue
                key = f"char:{name}"
                entries[key] = StoryBibleEntry(
                    key=key,
                    category="character",
                    display_name=str(name),
                    content=str(info.get("summary") or info),
                    activation_keywords=[str(name)],
                    priority=600,
                )
        loops = [
            OpenLoop.from_dict(x)
            for x in (data.get("open_loops") or data.get("open_threads") or [])
            if isinstance(x, dict)
        ]
        if not loops:
            for t in data.get("open_threads") or []:
                ts = str(t).strip()
                if ts:
                    loops.append(OpenLoop(loop_id=uuid4().hex[:12], description=ts))
        timeline = [
            TimelineEvent.from_dict(x)
            for x in (data.get("timeline") or [])
            if isinstance(x, dict)
        ]
        return cls(
            entries=entries,
            open_loops=loops,
            timeline=timeline,
            style_contract=StyleContract.from_dict(data.get("style_contract")),
            version=int(data.get("version") or 0),
            updated_at=str(data.get("updated_at") or ""),
            chapters=dict(data.get("chapters") or {}),
            characters=dict(data.get("characters") or {}),
            checkpoints=list(data.get("checkpoints") or []),
        )


@dataclass
class NarrativeEvent:
    event_id: str
    chapter_index: int
    event_type: EventType
    subject: str
    detail: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NarrativeEvent:
        et = str(data.get("event_type") or "plot_point_advanced")
        allowed = {
            "character_introduced",
            "character_state_changed",
            "relationship_changed",
            "plot_point_advanced",
            "foreshadow_planted",
            "foreshadow_resolved",
            "conflict_escalated",
            "conflict_resolved",
            "setting_established",
            "timeline_advanced",
        }
        if et not in allowed:
            et = "plot_point_advanced"
        return cls(
            event_id=str(data.get("event_id") or uuid4().hex),
            chapter_index=int(data.get("chapter_index") or 0),
            event_type=et,  # type: ignore[assignment]
            subject=str(data.get("subject") or ""),
            detail=str(data.get("detail") or ""),
            metadata=dict(data.get("metadata") or {}),
            created_at=str(data.get("created_at") or ""),
        )


@dataclass
class ChapterQualityRubric:
    continuity_score: float = 0.0
    outline_alignment: float = 0.0
    character_consistency: float = 0.0
    duplication_risk: float = 0.0
    chapter_completion: float = 0.0
    hook_quality: float = 0.0

    @property
    def composite_score(self) -> float:
        weights = {
            "continuity": 0.25,
            "alignment": 0.20,
            "character": 0.20,
            "duplication": 0.15,
            "completion": 0.10,
            "hook": 0.10,
        }
        return (
            self.continuity_score * weights["continuity"]
            + self.outline_alignment * weights["alignment"]
            + self.character_consistency * weights["character"]
            + (1 - self.duplication_risk) * weights["duplication"]
            + self.chapter_completion * weights["completion"]
            + self.hook_quality * weights["hook"]
        )

    def pass_gate(self, *, threshold: float = 0.65) -> bool:
        return (
            self.composite_score >= threshold
            and self.duplication_risk < 0.5
            and self.continuity_score >= 0.4
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["composite_score"] = round(self.composite_score, 4)
        d["pass_gate"] = self.pass_gate()
        return d

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> ChapterQualityRubric:
        if not isinstance(data, dict):
            return cls()
        return cls(
            continuity_score=float(data.get("continuity_score") or 0),
            outline_alignment=float(data.get("outline_alignment") or 0),
            character_consistency=float(data.get("character_consistency") or 0),
            duplication_risk=float(data.get("duplication_risk") or 0),
            chapter_completion=float(data.get("chapter_completion") or 0),
            hook_quality=float(data.get("hook_quality") or 0),
        )


@dataclass
class ChapterOutcome:
    chapter_index: int
    events: list[NarrativeEvent] = field(default_factory=list)
    chapter_summary: str = ""
    ending_state: str = ""
    hook_for_next: str = ""
    quality_rubric: Optional[ChapterQualityRubric] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_index": self.chapter_index,
            "events": [e.to_dict() for e in self.events],
            "chapter_summary": self.chapter_summary,
            "ending_state": self.ending_state,
            "hook_for_next": self.hook_for_next,
            "quality_rubric": self.quality_rubric.to_dict() if self.quality_rubric else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChapterOutcome:
        events = [
            NarrativeEvent.from_dict(e)
            for e in (data.get("events") or [])
            if isinstance(e, dict)
        ]
        qr = data.get("quality_rubric")
        return cls(
            chapter_index=int(data.get("chapter_index") or 0),
            events=events,
            chapter_summary=str(data.get("chapter_summary") or data.get("summary") or ""),
            ending_state=str(data.get("ending_state") or ""),
            hook_for_next=str(data.get("hook_for_next") or data.get("hook") or ""),
            quality_rubric=ChapterQualityRubric.from_dict(qr) if qr else None,
        )


@dataclass
class PlotPoint:
    chapter_index: int
    label: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OutlineDiffResult:
    added_plot_points: list[PlotPoint] = field(default_factory=list)
    removed_plot_points: list[PlotPoint] = field(default_factory=list)
    modified_plot_points: list[tuple[PlotPoint, PlotPoint]] = field(default_factory=list)
    affected_chapters: list[int] = field(default_factory=list)
    severity: DiffSeverity = "minor"
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "added_plot_points": [p.to_dict() for p in self.added_plot_points],
            "removed_plot_points": [p.to_dict() for p in self.removed_plot_points],
            "modified_plot_points": [
                {"old": o.to_dict(), "new": n.to_dict()} for o, n in self.modified_plot_points
            ],
            "affected_chapters": self.affected_chapters,
            "severity": self.severity,
            "summary": self.summary,
        }


@dataclass
class BridgeSpec:
    insert_after_chapter: int
    target_chars: int
    bridge_goal: str
    must_resolve: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PatchInstruction:
    chapter_index: int
    patch_type: PatchType
    target_section: str
    instruction: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AlignmentDecision:
    change_level: ChangeLevel
    body_action: BodyAction
    reason: str
    affected_chapters: list[int] = field(default_factory=list)
    bridge_spec: Optional[BridgeSpec] = None
    patch_instructions: list[PatchInstruction] = field(default_factory=list)
    continuity_risks: list[str] = field(default_factory=list)
    confidence: float = 0.8

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_level": self.change_level,
            "body_action": self.body_action,
            "reason": self.reason,
            "affected_chapters": self.affected_chapters,
            "bridge_spec": self.bridge_spec.to_dict() if self.bridge_spec else None,
            "patch_instructions": [p.to_dict() for p in self.patch_instructions],
            "continuity_risks": self.continuity_risks,
            "confidence": self.confidence,
        }


@dataclass
class StoryState:
    characters: dict[str, str] = field(default_factory=dict)
    relationships: dict[str, str] = field(default_factory=dict)
    open_foreshadows: list[str] = field(default_factory=list)
    resolved_foreshadows: list[str] = field(default_factory=list)
    conflicts: dict[str, str] = field(default_factory=dict)
    timeline_position: str = ""
    settings: dict[str, str] = field(default_factory=dict)

    def apply(self, event: NarrativeEvent) -> None:
        et = event.event_type
        subj = event.subject.strip()
        if et == "character_introduced":
            self.characters[subj] = event.detail
        elif et == "character_state_changed":
            self.characters[subj] = event.detail
        elif et == "relationship_changed":
            self.relationships[subj] = event.detail
        elif et == "foreshadow_planted":
            if subj and subj not in self.open_foreshadows:
                self.open_foreshadows.append(subj)
        elif et == "foreshadow_resolved":
            if subj in self.open_foreshadows:
                self.open_foreshadows.remove(subj)
            if subj and subj not in self.resolved_foreshadows:
                self.resolved_foreshadows.append(subj)
        elif et == "conflict_escalated":
            self.conflicts[subj] = event.detail
        elif et == "conflict_resolved":
            self.conflicts.pop(subj, None)
        elif et == "setting_established":
            self.settings[subj] = event.detail
        elif et == "timeline_advanced":
            self.timeline_position = event.detail or subj
        elif et == "plot_point_advanced":
            if subj:
                self.settings.setdefault("plot", event.detail)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
