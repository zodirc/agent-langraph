from __future__ import annotations

from typing import Any, Optional

from app.domain.packs.base import DomainPack
from app.runtime.state import AgentState
from app.services.mission_schema import (
    build_mission_dict,
    resolve_writing_intent_for_step,
)


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
        return {
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
            from app.services.writing_phases import should_use_writing_llm_decide

            if should_use_writing_llm_decide(mission):
                from app.services.writing_phases import suggest_writing_phase_fallback

                return suggest_writing_phase_fallback(state).to_dict()
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

    def build_writing_intent_for_state(self, state: AgentState) -> dict[str, Any]:
        return resolve_writing_intent_for_step(state, mission=state.get("mission") or {})


WRITING_PACK = WritingPack(
    name="writing",
    description="Long-form artifact writing with step_policy and metric completion",
    tools=["read_text_artifact"],
    planning_hints=["mission.step_policy", "append_body per step"],
    system_prompt=(
        "Long-form writing: one step_policy step per mission loop; "
        "never put full book in one artifact call."
    ),
    risk_level="LOW",
    default_max_steps=500,
    default_execution_mode="autonomous",
    metadata={"domains": ["writing", "novel", "report", "longform"]},
)
