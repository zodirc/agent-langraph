from __future__ import annotations

from typing import Any, Optional

from app.domain.packs.base import DomainPack
from app.runtime.state import AgentState
from app.services.action_resolver import CandidateAction, TaskSnapshot
from app.services.mission_schema import build_mission_dict


class WritingPack(DomainPack):
    """Long-form writing — mission step_policy + written_chars metric."""

    def parse_mission(
        self,
        state: AgentState,
        payload: dict[str, Any],
        *,
        mission_block: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        block = mission_block or {}
        return build_mission_dict(
            state,
            payload,
            kind="writing",
            mission_block=block,
        )

    def build_task_snapshot(
        self,
        state: AgentState,
        mission: dict[str, Any],
    ) -> TaskSnapshot:
        from app.config.settings import settings
        from app.domain.mission import StepPolicy
        from app.services.manuscript_context import parse_last_chapter_index, read_body_text
        from app.services.manuscript_service import resolve_manuscript
        from app.services.mission_intervention import intervention_from_payload, is_forced

        policy = StepPolicy.from_dict(mission.get("step_policy") or {})
        ms = resolve_manuscript(state["task_id"], state.get("manuscript"))
        stored_ms = state.get("manuscript") or {}
        outline_bytes = max(int(ms.outline_bytes or 0), int(stored_ms.get("outline_bytes") or 0))
        body_bytes = max(int(ms.body_bytes or 0), int(stored_ms.get("body_bytes") or 0))
        outline_path = ms.outline_path or stored_ms.get("outline_path")
        body_path = ms.body_path or stored_ms.get("body_path")
        min_body = int(getattr(settings, "MANUSCRIPT_MIN_BODY_CHARS", 200))
        min_outline = int(getattr(settings, "MANUSCRIPT_MIN_OUTLINE_CHARS", 80))
        has_outline = bool(outline_path) and outline_bytes > 0
        has_body = bool(body_path) and body_bytes >= min_body
        last_ch = 0
        if has_body and ms.body_path:
            last_ch = parse_last_chapter_index(
                read_body_text(state["task_id"], ms.body_path, state=state)
            )
        payload = state.get("input_payload") or {}
        intervention = intervention_from_payload(payload)
        forced = None
        if intervention and is_forced(intervention):
            forced = str(intervention.get("action") or "")
        elif intervention:
            forced = str(intervention.get("action") or "") or None
        target = float((mission.get("success_criteria") or {}).get("target") or 0)
        failure_count = int(state.get("retry_count") or 0) + len(state.get("errors") or [])
        return TaskSnapshot(
            has_bootstrap_artifact=has_outline,
            has_main_artifact=has_body,
            main_artifact_bytes=body_bytes,
            target_bytes=int(target),
            step_index=int(state.get("mission_step") or 1),
            forced_intervention=forced,
            pending_review=forced == "review_outline",
            failure_count=failure_count,
            policy_first_step=str(policy.first_step or "outline"),
            policy_then=str(policy.then or "append_body"),
            outline_max_chars=int(policy.outline_max_chars or 500),
            chars_per_step=int(policy.chars_per_step or 3000),
            min_outline_chars=min_outline,
            min_body_chars=min_body,
            last_chapter_index=last_ch,
            revision=int(ms.revision or 0),
        )

    def resolve_artifact_names(
        self,
        policy: dict[str, Any],
        *,
        mission: Optional[dict[str, Any]] = None,
    ) -> dict[str, str]:
        from app.domain.mission import StepPolicy

        sp = StepPolicy.from_dict(policy or (mission or {}).get("step_policy") or {})
        return {
            "bootstrap_artifact": sp.outline_artifact,
            "main_artifact": sp.body_artifact,
            "outline_filename": sp.outline_artifact,
            "novel_filename": sp.body_artifact,
        }

    def collect_metrics(
        self,
        state: AgentState,
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        from app.services.manuscript_metrics import body_written_chars

        manuscript = state.get("manuscript") or observation.get("manuscript") or {}
        body_bytes = int(manuscript.get("body_bytes") or 0)
        written = body_written_chars(
            state["task_id"],
            manuscript,
            state=state,
        )
        mission = state.get("mission") or {}
        target = float((mission.get("success_criteria") or {}).get("target") or 0)
        sp = mission.get("step_policy") or {}
        payload = state.get("input_payload") or {}
        progress = state.get("progress") or {}
        quality = (progress.get("metrics") or {}).get("chapter_quality") or payload.get(
            "last_chapter_outcome", {}
        ).get("quality_rubric")
        metrics = {
            "written_chars": written,
            "body_bytes": body_bytes,
            "body_path": manuscript.get("body_path"),
            "target_chars": target,
            "chars_per_step": int(sp.get("chars_per_step") or 0),
            "progress_pct": round(100.0 * written / target, 2) if target > 0 else 0.0,
            "has_reasoning": bool(state.get("reasoning_result")),
            "outline_bytes": int(manuscript.get("outline_bytes") or 0),
            "chapter_cursor": int(manuscript.get("chapter_cursor") or 0),
            "last_chapter_index": int(manuscript.get("last_chapter_index") or 0),
            "revision": int(manuscript.get("revision") or 0),
        }
        if quality:
            metrics["chapter_quality"] = quality
            metrics["quality_pass_gate"] = bool(quality.get("pass_gate"))
            metrics["quality_composite"] = quality.get("composite_score")
        return metrics

    def evaluate_success(
        self,
        mission: dict[str, Any],
        progress: dict[str, Any],
        observation: dict[str, Any],
    ) -> tuple[bool, str]:
        if observation.get("has_failures"):
            return False, "last step failed"
        criteria = mission.get("success_criteria") or {}
        if criteria.get("type") != "metric_gte":
            return False, "writing mission requires metric_gte criteria"
        target = float(criteria.get("target") or 0)
        if target <= 0:
            return False, "total_target_chars not set on mission"
        written = float((progress.get("metrics") or {}).get("written_chars", 0))
        if written >= target:
            return True, f"written_chars {written} >= {target}"
        return False, f"written_chars {written} < {target}"

    def suggest_step_decision(
        self,
        state: AgentState,
        *,
        mission: dict[str, Any],
        progress: dict[str, Any],
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        from app.config.settings import settings
        from app.services.mission_intervention import intervention_from_payload, is_forced

        payload = state.get("input_payload") or {}
        intervention = intervention_from_payload(payload)
        from app.services.mission_orchestrator import (
            get_current_work_item,
            orchestration_enabled,
            work_plan_completed,
        )

        if orchestration_enabled(mission):
            if work_plan_completed(state):
                return {"action": "finish", "rationale": "orchestrated work plan done"}
            item = get_current_work_item(state)
            if item:
                kind = str(item.get("kind") or "")
                if kind == "human_gate":
                    return {
                        "action": "continue",
                        "next_executor": "subgraph:writing",
                        "params": {},
                        "rationale": "human gate work item",
                    }
                if kind == "edit_plot":
                    return {
                        "action": "continue",
                        "next_executor": "pipeline:request",
                        "params": {},
                        "rationale": "edit_plot work item",
                    }
                if kind in ("patch_recent_chapter", "consistency_check", "reconcile_outline_body"):
                    return {
                        "action": "continue",
                        "next_executor": "pipeline:request",
                        "params": {},
                        "rationale": f"tool-assisted work item: {kind}",
                    }
                return {
                    "action": "continue",
                    "next_executor": "subgraph:writing",
                    "params": {},
                    "rationale": f"work item: {item.get('title')}",
                }

        if intervention and is_forced(intervention):
            action = intervention["action"]
            executor = (
                "pipeline:request"
                if action in ("edit_plot", "run_tools")
                else "subgraph:writing"
            )
            return {
                "action": "continue",
                "next_executor": executor,
                "params": {},
                "rationale": f"forced intervention: {action}",
            }

        metrics = progress.get("metrics") or {}
        last_ch = int(metrics.get("last_chapter_index") or 0)
        quality = metrics.get("chapter_quality") or {}
        if quality and not quality.get("pass_gate", True):
            return {
                "action": "continue",
                "next_executor": "subgraph:writing",
                "params": {
                    "writing_phase": "review_chapter",
                    "chapter_index": last_ch or metrics.get("chapter_cursor"),
                },
                "rationale": "chapter quality below gate — review",
            }
        if quality.get("polish_recommended") or float(quality.get("duplication_risk") or 0) > 0.4:
            return {
                "action": "continue",
                "next_executor": "subgraph:writing",
                "params": {
                    "writing_phase": "polish_chapter",
                    "chapter_index": last_ch or metrics.get("chapter_cursor"),
                },
                "rationale": "high duplication risk — polish",
            }
        interval = int(getattr(settings, "MISSION_WRITING_REVIEW_EVERY_CHAPTERS", 0))
        if interval > 0 and last_ch > 0 and last_ch % interval == 0:
            constraints = mission.get("constraints") or {}
            if not constraints.get("no_human"):
                return {
                    "action": "pause",
                    "next_executor": "pipeline:request",
                    "params": {},
                    "rationale": f"chapter checkpoint at {last_ch}",
                }

        success, reason = self.evaluate_success(mission, progress, observation)
        if success:
            return {"action": "finish", "rationale": reason}
        return {
            "action": "continue",
            "next_executor": "subgraph:writing",
            "params": {},
            "rationale": reason or "writing step toward total_target_chars",
        }

    def candidate_actions(self, snapshot: TaskSnapshot) -> list[CandidateAction]:
        """Writing-domain candidate actions from objective snapshot."""
        actions: list[CandidateAction] = []
        forced = snapshot.forced_intervention

        if forced == "reset_body":
            return [
                CandidateAction(
                    "reset_body",
                    True,
                    1.0,
                    "forced reset_body intervention",
                )
            ]
        if forced == "edit_plot":
            return [
                CandidateAction(
                    "edit_plot",
                    True,
                    1.0,
                    "forced edit_plot intervention",
                )
            ]
        if forced == "rewrite_outline":
            return [
                CandidateAction(
                    "write_outline",
                    True,
                    1.0,
                    "forced rewrite_outline intervention",
                    metadata={"source": "rewrite_outline"},
                )
            ]
        if forced == "review_outline":
            return [
                CandidateAction(
                    "review_outline",
                    True,
                    1.0,
                    "forced review_outline intervention",
                )
            ]

        if snapshot.policy_first_step == "outline" and not snapshot.has_bootstrap_artifact:
            actions.append(
                CandidateAction(
                    "write_outline",
                    True,
                    0.95,
                    "outline missing",
                )
            )
        if snapshot.has_bootstrap_artifact and not snapshot.has_main_artifact:
            actions.append(
                CandidateAction(
                    "write_body",
                    True,
                    0.85,
                    "body not started",
                )
            )
        if snapshot.has_main_artifact:
            then = (
                snapshot.policy_then
                if snapshot.policy_then in ("append_body", "write_body")
                else "append_body"
            )
            actions.append(
                CandidateAction(
                    then,
                    True,
                    0.75,
                    "continue main artifact",
                    metadata={"chapter_index": max(1, snapshot.last_chapter_index + 1)},
                )
            )
        return actions

    def map_action_to_intent(
        self,
        action: CandidateAction,
        state: AgentState,
        mission: dict[str, Any],
    ) -> dict[str, Any]:
        """Map selected candidate to writing_intent dict."""
        from app.config.settings import settings
        from app.domain.mission import StepPolicy
        from app.services.manuscript_service import resolve_manuscript

        policy = StepPolicy.from_dict(mission.get("step_policy") or {})
        step = int(state.get("mission_step") or 1)
        payload = state.get("input_payload") or {}
        meta = dict(action.metadata or {})
        ms = resolve_manuscript(state["task_id"], state.get("manuscript"))
        min_body = int(getattr(settings, "MANUSCRIPT_MIN_BODY_CHARS", 200))
        min_outline = int(getattr(settings, "MANUSCRIPT_MIN_OUTLINE_CHARS", 80))

        if action.action == "reset_body":
            return {
                "enabled": True,
                "action": "reset_body",
                "target_chars": policy.chars_per_step,
                "min_chars": min_body,
                "source": "mission_intervention",
                "mission_step": step,
                "chapter_index": 1,
                "revision": ms.revision,
                "require_read_first": True,
            }
        if action.action == "edit_plot":
            return {
                "enabled": False,
                "action": "edit_plot",
                "source": "mission_intervention",
                "mission_step": step,
                "edit_spec": dict(payload.get("edit_plot_spec") or {}),
            }
        if action.action == "review_outline":
            return {
                "enabled": False,
                "action": "review_outline",
                "source": "mission_intervention",
                "mission_step": step,
            }
        if action.action == "write_outline":
            source = (
                "mission_intervention"
                if meta.get("source") == "rewrite_outline"
                else "mission_step_policy"
            )
            intent: dict[str, Any] = {
                "enabled": True,
                "action": "write_outline",
                "target_chars": policy.outline_max_chars,
                "min_chars": min_outline,
                "source": source,
                "mission_step": step,
            }
            if source == "mission_intervention":
                intent["revision"] = ms.revision
                intent["require_read_first"] = True
            return intent
        if action.action == "write_body":
            return {
                "enabled": True,
                "action": "write_body",
                "target_chars": policy.chars_per_step,
                "min_chars": min_body,
                "source": "mission_step_policy",
                "mission_step": step,
                "chapter_index": 1,
                "require_read_first": False,
            }
        chapter_index = int(
            meta.get("chapter_index")
            or max(1, int((state.get("manuscript") or {}).get("last_chapter_index") or 0) + 1)
        )
        return {
            "enabled": True,
            "action": action.action,
            "target_chars": policy.chars_per_step,
            "min_chars": min_body,
            "source": "mission_step_policy",
            "mission_step": step,
            "chapter_index": chapter_index,
        }

    def map_intent_to_work_item(
        self,
        intent: dict[str, Any],
        mission: dict[str, Any],
        *,
        step: int,
    ) -> Optional[dict[str, Any]]:
        """Build lazy work_plan item from writing_intent."""
        from app.domain.mission import StepPolicy

        if not intent.get("enabled"):
            action = str(intent.get("action") or "")
            if action == "edit_plot":
                return {
                    "id": f"wi-step-{step}",
                    "kind": "edit_plot",
                    "title": "edit_plot",
                    "status": "pending",
                    "params": {"edit_spec": intent.get("edit_spec") or {}},
                }
            if action == "human_gate":
                return {
                    "id": f"wi-gate-{step}",
                    "kind": "human_gate",
                    "title": "human_gate",
                    "status": "pending",
                    "params": {},
                }
            return None

        action = str(intent.get("action") or "append_body")
        policy = StepPolicy.from_dict(mission.get("step_policy") or {})
        if action == "write_outline":
            kind = "write_outline"
            title = "write_outline"
        elif action == "write_body":
            kind = "write_body"
            title = "write_body"
        else:
            kind = "append_chapter"
            title = f"append chapter {intent.get('chapter_index', '?')}"

        return {
            "id": f"wi-step-{step}",
            "kind": kind,
            "title": title,
            "status": "pending",
            "params": {
                "target_chars": intent.get("target_chars") or policy.chars_per_step,
                "chapter_index": intent.get("chapter_index"),
                "require_read_first": intent.get("require_read_first"),
            },
        }

    def work_item_to_intent(
        self,
        item: dict[str, Any],
        *,
        mission: dict[str, Any],
        mission_step: int,
    ) -> dict[str, Any]:
        from app.config.settings import settings
        from app.domain.mission import StepPolicy

        kind = str(item.get("kind") or "")
        params = dict(item.get("params") or {})
        policy = StepPolicy.from_dict(mission.get("step_policy") or {})
        base = {
            "enabled": True,
            "source": "work_plan",
            "mission_step": mission_step,
            "work_item_id": item.get("id"),
            "work_item_title": item.get("title"),
        }
        if kind == "write_outline":
            return {
                **base,
                "action": "write_outline",
                "target_chars": int(params.get("target_chars") or policy.outline_max_chars),
                "min_chars": int(getattr(settings, "MANUSCRIPT_MIN_OUTLINE_CHARS", 80)),
                "require_read_first": bool(params.get("require_read_first")),
            }
        if kind in ("append_chapter", "append_body"):
            return {
                **base,
                "action": "append_body",
                "target_chars": int(params.get("target_chars") or policy.chars_per_step),
                "min_chars": int(getattr(settings, "MANUSCRIPT_MIN_BODY_CHARS", 200)),
                "chapter_index": int(params.get("chapter_index") or 1),
            }
        if kind == "write_body":
            return {
                **base,
                "action": "write_body",
                "target_chars": int(params.get("target_chars") or policy.chars_per_step),
                "min_chars": int(getattr(settings, "MANUSCRIPT_MIN_BODY_CHARS", 200)),
                "chapter_index": int(params.get("chapter_index") or 1),
            }
        if kind == "bridge_chapter":
            return {
                **base,
                "action": "append_body",
                "target_chars": int(
                    params.get("target_chars")
                    or getattr(settings, "WRITING_BRIDGE_DEFAULT_CHARS", 600)
                ),
                "min_chars": int(getattr(settings, "MANUSCRIPT_MIN_BODY_CHARS", 200)),
                "chapter_index": int(params.get("chapter_index") or 1),
                "bridge_spec": dict(params.get("bridge_spec") or {}),
                "phase_notes": str(params.get("phase_notes") or "bridge_chapter"),
            }
        if kind == "patch_recent_chapter":
            return {
                **base,
                "action": "append_body",
                "target_chars": int(params.get("target_chars") or policy.chars_per_step),
                "min_chars": int(getattr(settings, "MANUSCRIPT_MIN_BODY_CHARS", 200)),
                "chapter_index": int(params.get("chapter_index") or 1),
                "patch_instructions": list(params.get("patch_instructions") or []),
                "phase_notes": str(params.get("phase_notes") or "patch_recent_chapter"),
            }
        if kind == "reconcile_outline_body":
            return {
                **base,
                "action": "consistency_check",
                "chapter_index": int(params.get("chapter_index") or 1),
                "phase_notes": str(params.get("phase_notes") or "reconcile_outline_body"),
            }
        if kind == "reset_body":
            return {
                **base,
                "action": "reset_body",
                "target_chars": int(params.get("target_chars") or policy.chars_per_step),
                "min_chars": int(getattr(settings, "MANUSCRIPT_MIN_BODY_CHARS", 200)),
                "chapter_index": 1,
                "require_read_first": bool(params.get("require_read_first", True)),
            }
        if kind == "edit_plot":
            return {
                **base,
                "enabled": False,
                "action": "edit_plot",
                "edit_spec": params.get("edit_spec") or {},
            }
        if kind == "run_tools":
            return {**base, "enabled": False, "action": "run_tools"}
        if kind == "human_gate":
            return {**base, "enabled": False, "action": "human_gate"}
        if kind in (
            "consistency_check",
            "review_chapter",
            "polish_chapter",
            "chapter_summary",
            "arc_checkpoint",
        ):
            return {
                **base,
                "action": kind,
                "chapter_index": int(params.get("chapter_index") or 1),
            }
        return {**base, "enabled": False, "action": kind}

    def build_writing_intent_for_state(self, state: AgentState) -> dict[str, Any]:
        from app.services.mission_schema import resolve_writing_intent_for_step

        return resolve_writing_intent_for_step(state, mission=state.get("mission") or {})


WRITING_PACK = WritingPack(
    name="writing",
    description="Long-form artifact writing with step_policy and metric completion",
    tools=[
        "read_text_artifact",
        "ls_path",
        "read_file",
        "grep_file",
        "replace_in_file",
    ],
    planning_hints=[
        "mission.step_policy",
        "append_body per step",
        "batch_unit_quality: work_plan_patch review_chapter per written chapter",
    ],
    system_prompt=(
        "Long-form writing: one step_policy step per mission loop; "
        "never put full book in one artifact call."
    ),
    risk_level="LOW",
    default_max_steps=500,
    default_execution_mode="autonomous",
    metadata={"domains": ["writing", "novel", "report", "longform"]},
)
