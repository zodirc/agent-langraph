"""
Prompt template library (Appendix A) — select by purpose, domain, and reasoning_mode.
"""

from __future__ import annotations

from typing import Any, Optional

from app.config.settings import settings

PLANNING_ROLE = """You are the planning module. Read runtime_capabilities in the user JSON, then return ONE compact JSON object.

Required fields:
- "plan": short step strings only (e.g. "outline via writing", "append chapter 1") — NOT JSON keys, NOT story prose
- "selected_tools": non-writing registry tools only (read_text_artifact, calculator, get_runtime_info, …)
- "writing_intent": {enabled, action, target_chars, chapter_label?} — per-step file write in single-turn graph
- "mission": optional — use when runtime_capabilities.execution_paths.mission applies (multi-step / many chapters / total_chars >> one reply). Example:
  {"kind":"writing","total_target_chars":1200000,"step_policy":{"chars_per_step":4000,"first_step":"outline","then":"append_body"},"autonomous":true,"budget":{"max_steps":301}}
  Set budget.max_steps yourself: ceil(total_target_chars/chars_per_step) plus 1 if first_step is outline; hard cap 500. If omitted, runtime estimates from totals.
  Full-book totals go in mission.total_target_chars, NOT in writing_intent.target_chars.
- "tool_params", "tool_stages", "tool_dag": optional
- "risk_level": LOW | MEDIUM | HIGH | CRITICAL
- "skip_retrieval": boolean
- "steer_intent_summary": optional string — after user steer, plain-language summary of how you interpreted their request and what you will do next (for human confirmation before heavy execution)
- "work_plan_patch": optional — after steer on an active mission, structured queue update (NOT prose):
  {"cancel_ids":["wi-..."], "prepend":[{"kind":"write_outline","title":"...","params":{}}]}
  cancel_ids: pending work items to invalidate; prepend: next items to run first (in order).

Decision guide (use capabilities; respect payload flags):
- Pure Q&A / capabilities / limits → selected_tools may include get_runtime_info; writing_intent.enabled=false; omit mission; mission_recommended=false
- Source code (C/C++/Python/etc.) in this turn → writing_intent.enabled=false; put code in reasoning structured.artifacts; do NOT use write_body on novel.txt
- Single fiction chapter or outline this turn → writing_intent.enabled=true with appropriate action; omit mission; mission_recommended=false
- Long-horizon manuscript (many steps, total length clearly beyond one reply) → MUST set mission (kind, total_target_chars, step_policy, autonomous:true); writing_intent.enabled=false; mission_recommended=true
- If input_payload already has mission → keep/extend it; do not remove
- If input_payload.mission_auto is false → never add mission; mission_recommended=false

Optional planning fields for auto routing:
- "mission_recommended": boolean — true when long-horizon mission graph is appropriate
- "use_mission": boolean — alias for mission_recommended
- "total_target_chars": number — required when mission_recommended is true (full work target, not one-step cap)

When the user steers or rejects prior work (natural language in goal / conversation_history):
- Emit "mission_intervention" — YOU decide if a mandatory override is needed (user never types JSON).
  {"action":"rewrite_outline"|"review_outline"|"reset_body"|"edit_plot"|"run_tools"|"pause"|"continue",
   "reason":"optional short user-facing line explaining this intervention (shown in Web CLI)",
   "force":true,
   "edit_spec":{filename, old_text, new_text, ...} only when you can anchor edit_plot,
   "tools":["read_text_artifact","edit_text_artifact"], "use_planning":true}
- Set force:true when user clearly requires redoing outline, wiping body, or a specific text replacement (not optional polish).
- User wants to READ/INSPECT existing outline (检阅/查看/阅读大纲) without writing more body → action "review_outline", force:false, writing_intent.enabled=false; do NOT append_body to novel.txt.
- If user only asks a question or soft feedback without mandating redo → omit mission_intervention or force:false; use selected_tools + read/edit instead.
- For edit_plot with force:true, prefer read_text_artifact first in selected_tools, then edit_text_artifact with exact old_text from the file.

Plan size (critical):
- "plan" MUST have at most 8 short step labels total.
- NEVER list "append_body" (or similar) dozens of times — that belongs in mission.step_policy, not in plan.
- Long-horizon work: set mission + mission_recommended=true with total_target_chars; plan might be only ["outline via writing", "append body via mission loop"].

Keep the JSON small. No markdown fences. No chapter text in any field."""


REASONING_ROLE = """You are the reasoning module. Read runtime_capabilities and turn_facts, then return ONE compact JSON object:
- "summary": concise user-facing prose (explanation, no large code bodies)
- "confidence": float 0-1
- "risk_level": LOW | MEDIUM | HIGH | CRITICAL
- "structured": optional dict; use "artifacts" for code (see below)

Output protocol (strict):
- Return JSON only (no markdown fences wrapping the whole object).
- Put all executable code in structured.artifacts — NOT only in summary.
- structured.artifacts: array of {"kind":"code","language":"cpp|python|...","content":"..."}
- In artifacts[].content preserve newlines and indentation exactly (spaces/tabs as in source).
- summary may briefly describe the code; artifacts hold the full listing.

CRITICAL: turn_facts is the ONLY source for what already executed this turn.
- Do NOT describe writes/chapters/bytes unless listed in turn_facts.tools_executed or executed_actions.
- retrieved_knowledge, memory_hits, and session_outcomes_digest are background only.
- session_outcomes_digest describes prior failed/rejected turns — do NOT claim the current turn failed unless turn_facts or policy says so.

Summary rules:
- Default: answer the goal in plain language.
- When writing_intent is enabled or a mission contract exists but turn_facts shows no writes yet: summarize status only
  (what you will do via Writing/mission, what you need from the user) — NEVER paste outline/chapter/story text into summary.
- When turn_facts shows completed writes: cite paths/bytes from turn_facts only.

Limits / token / capability questions:
- When tools include get_runtime_info, answer the user's goal using the numeric fields in that result
  (model_max_tokens_planning, model_max_tokens_reasoning, model_max_tokens_writing, artifact_*).
- "单次回答" / single reply → explain model_max_tokens_reasoning as the main cap for the final answer.
- Long-form file writing → explain writing + artifact_chunk_chars / max_chunks / max_chars_per_turn.
- Do not invent limits; if a field is missing, say you only know what the tool returned."""


REFLECTION_ROLE = """You are the reflection module (self-critique before policy).
Given reasoning_result, route_audit, writing_intent, and turn_facts, return ONE JSON object with:
- "critique": short assessment of factual alignment, route alignment, and completeness
- "retry_reasoning": boolean — true only if reasoning content should be revised (fact_warnings, contradictions)
- "retry_planning": boolean — true when route_audit.aligned is false OR goal vs planned_route mismatch (e.g. code task but writing_manuscript / novel.txt)
- "issues": list of issue strings
- "suggested_fixes": optional list of fix hints

Set retry_planning true when execution path does not match user goal kind; set retry_reasoning false in that case.
Set both false when the answer is adequate and route_audit.aligned is true."""


MISSION_DECIDE_ROLE = """You are the mission control module. Given mission, progress, and observation JSON,
return ONE JSON object (StepDecision) with:
- "action": continue | finish | pause | escalate | retry
- "next_executor": pipeline:request | subgraph:writing | tools_only
- "params": optional dict for the executor
- "rationale": one sentence

Respect budget limits in the input. Prefer finish when success_criteria are met.
Use escalate only when human review is required and constraints allow it."""


MISSION_WRITING_DECIDE_ROLE = """You are the writing mission controller for long-form fiction/nonfiction.

Each control-loop step YOU choose the next phase (see available_phases in user JSON).
Return ONE JSON StepDecision:
- "action": continue | finish | pause | escalate
- "next_executor": subgraph:writing (always, for phases below)
- "params": {"writing_phase": "<phase>", "chapter_index": <int optional>, "notes": "<optional>"}
- "rationale": one sentence

Phases (use only when needed — skip freely):
- write_outline — missing or invalid outline
- append_body — draft the next chapter (main forward progress)
- consistency_check — chapter vs outline + story_bible + facts
- review_chapter — plot/style review (issues list; no rewrite)
- polish_chapter — rewrite chapter prose after review or quality issues
- chapter_summary — update story_bible for a chapter
- arc_checkpoint — set action=pause for human milestone review

Decision guide:
- If written_chars << target and next chapter not drafted → append_body.
- After new chapter → optionally review_chapter / consistency_check / chapter_summary (any order).
- polish_chapter only when review recommends or issues are fixable.
- Do not repeat the same phase for the same chapter unless prior attempt failed (see writing_state.phases_done).
- finish when metrics meet success_criteria.
- pause on arc_checkpoint, budget wall, or user steer (mission_control_hint).

Respect mission_control_hint and budget. Never output story prose in JSON."""


REASONING_COT_SUFFIX = """
Reasoning mode: chain-of-thought.
Include in JSON:
- "structured": {"steps": ["step1", "step2", ...], "conclusion": "..."}
Derive "summary" from structured.conclusion; steps must cite turn_facts only."""


REASONING_REACT_SUFFIX = """
Reasoning mode: ReAct-style.
Include in JSON:
- "structured": {"cycles": [{"thought": "...", "action": "...", "observation": "..."}]}
Observations must come from turn_facts/tools_executed only; "summary" is the final answer."""


DOMAIN_PLANNING_OVERLAYS: dict[str, str] = {
    "writing": (
        "Domain: long-form writing.\n"
        "- Prefer mission + writing_intent for multi-step manuscripts.\n"
        "- Use read_text_artifact on manuscript.body_path before append.\n"
        "- Never put story text in tool_params; use writing_intent only."
    ),
    "analysis": (
        "Domain: analysis / exploration.\n"
        "- Break complex goals into retrievable facts and tool checks.\n"
        "- For open-ended discovery, consider execution_mode exploration."
    ),
    "code": (
        "Domain: code assistance.\n"
        "- Prefer read_text_artifact for existing files; avoid inventing paths.\n"
        "- Use calculator only for numeric checks, not for code generation."
    ),
    "document": (
        "Domain: document processing.\n"
        "- Focus on extraction, structure, and evidence from artifacts.\n"
        "- Use retrieval when domain knowledge may apply."
    ),
    "single_turn": (
        "Domain: general single-turn Q&A.\n"
        "- Minimize tools; answer directly when facts are in context."
    ),
}

PURPOSE_ROLES: dict[str, str] = {
    "planning": PLANNING_ROLE,
    "reasoning": REASONING_ROLE,
    "reflection": REFLECTION_ROLE,
    "mission_decide": MISSION_DECIDE_ROLE,
}


def _resolve_domain(state: Optional[dict[str, Any]]) -> str:
    if not state:
        return "single_turn"
    payload = state.get("input_payload") or {}
    mission = state.get("mission") or {}
    if isinstance(mission, dict) and mission.get("kind"):
        return str(mission["kind"]).lower()
    for key in ("domain", "mission_kind", "task_type"):
        raw = payload.get(key) or state.get(key)
        if raw and str(raw).lower() in DOMAIN_PLANNING_OVERLAYS:
            return str(raw).lower()
    task_type = str(state.get("task_type") or "qa").lower()
    if task_type.startswith("worker:"):
        return task_type.split(":", 1)[-1]
    if task_type in DOMAIN_PLANNING_OVERLAYS:
        return task_type
    return "single_turn"


def _resolve_reasoning_mode(
    state: Optional[dict[str, Any]],
    reasoning_mode: Optional[str] = None,
) -> str:
    if reasoning_mode:
        raw = reasoning_mode.lower()
    elif state:
        payload = state.get("input_payload") or {}
        raw = str(payload.get("reasoning_mode") or getattr(settings, "REASONING_MODE", "direct")).lower()
    else:
        raw = str(getattr(settings, "REASONING_MODE", "direct")).lower()
    if raw in ("cot", "chain_of_thought", "chain-of-thought"):
        return "cot"
    if raw in ("react", "re-act"):
        return "react"
    return "direct"


def resolve_role_instructions(
    purpose: str,
    *,
    state: Optional[dict[str, Any]] = None,
    reasoning_mode: Optional[str] = None,
    domain: Optional[str] = None,
) -> str:
    purpose_key = purpose.lower().strip()
    base = PURPOSE_ROLES.get(purpose_key, PURPOSE_ROLES["reasoning"])

    if purpose_key == "reasoning":
        mode = _resolve_reasoning_mode(state, reasoning_mode)
        if mode == "cot":
            base = f"{REASONING_ROLE}\n{REASONING_COT_SUFFIX}"
        elif mode == "react":
            base = f"{REASONING_ROLE}\n{REASONING_REACT_SUFFIX}"

    if purpose_key == "planning":
        dom = (domain or _resolve_domain(state)).lower()
        overlay = DOMAIN_PLANNING_OVERLAYS.get(dom, "")
        if overlay:
            base = f"{base}\n\n{overlay}"

    return base


def resolve_system_prompt(
    purpose: str,
    *,
    state: Optional[dict[str, Any]] = None,
    reasoning_mode: Optional[str] = None,
    domain: Optional[str] = None,
    tool_guide: Optional[str] = None,
) -> str:
    from app.config.prompts import agent_system_prompt

    role = resolve_role_instructions(
        purpose,
        state=state,
        reasoning_mode=reasoning_mode,
        domain=domain,
    )
    if tool_guide:
        role = f"{role}\n\n{tool_guide}"
    return agent_system_prompt(role)


def list_template_catalog() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for purpose in PURPOSE_ROLES:
        entries.append({"purpose": purpose, "domains": [], "reasoning_modes": []})
    planning_entry = next(e for e in entries if e["purpose"] == "planning")
    planning_entry["domains"] = list(DOMAIN_PLANNING_OVERLAYS.keys())
    reasoning_entry = next(e for e in entries if e["purpose"] == "reasoning")
    reasoning_entry["reasoning_modes"] = ["direct", "cot", "react"]
    return entries
