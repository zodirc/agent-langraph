"""写作节点
流式 writing_delta via writing_stream；steer 可 preempt 长写作步。

Writing: artifact/manuscript generation when writing_intent.enabled.
Route: route_after_writing → reasoning | retry writing | dead_letter."""

from __future__ import annotations

import shutil
from typing import Any

from app.config.settings import settings
from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.artifact_content import (
    SteerPreempted,
    _build_append_chunks,
    generate_artifact_content,
    parse_requested_chars,
)
from app.services.execution_control import CancelRequested, PauseRequested
from app.services.step_committer import StepCommitter
from app.services.artifact_tools import (
    handle_append_text_artifact,
    handle_read_text_artifact,
    handle_write_text_artifact,
    task_artifact_dir,
)
from app.services.manuscript_context import (
    build_writing_context,
    extract_chapter_text,
    extract_outline_chapter_brief,
    is_near_duplicate_append,
    read_body_text,
    read_outline_text,
    sync_chapter_fields,
)
from app.services.manuscript_service import (
    Manuscript,
    resolve_body_filename,
    resolve_manuscript,
    resolve_outline_filename,
    validate_manuscript_content,
)
from app.services.manuscript_service import manuscript_has_body
from app.services.fact_layer import attach_turn_facts
from app.services.turn_event_log import record_turn_event
from app.services.reasoning_trace import report_block, report_status_trace
from app.services.state_store import get_state_store
from app.services.stream_progress import report_progress
from app.services.writing_phases import PHASE_ACTIONS, run_writing_phase


def _generate_validated_content(
    *,
    state: AgentState,
    tool_name: str,
    filename: str,
    goal: str,
    intent: dict[str, Any],
    target_chars: int,
    chunk_index: int = 0,
    chunk_total: int = 1,
) -> str:
    min_chars = int(intent.get("min_chars") or settings.MANUSCRIPT_MIN_BODY_CHARS)
    action = str(intent.get("action") or "append_body")
    last_error = ""

    for attempt in range(2):
        content = generate_artifact_content(
            state=state,
            tool_name=tool_name,
            filename=filename,
            goal=goal,
            target_chars=target_chars,
            chunk_index=chunk_index,
            chunk_total=chunk_total,
        )
        ok, reason = validate_manuscript_content(
            content, action=action, min_chars=min_chars
        )
        if ok:
            return content
        last_error = reason
        report_status_trace(
            "writing",
            f"内容校验未通过（{reason}），重试生成 ({attempt + 1}/2)…",
        )

    raise ValueError(f"Writing validation failed: {last_error}")


def writing_node(state: AgentState) -> AgentState:
    """
    Generate, validate, and persist long-form body/outline per writing_intent.

    Reads: input_payload.writing_intent, manuscript
    Writes: tool_results (append), manuscript, status, audit_log
    """
    try:
        from app.services.route_audit import writing_gate_allowed

        if not writing_gate_allowed(state):
            payload = dict(state.get("input_payload") or {})
            payload["skip_reasoning_after_tools"] = False
            return merge_state(
                state,
                input_payload=payload,
                status=TaskStatus.PLANNED.value,
                current_node="writing",
                audit_log=append_audit(
                    state,
                    "writing",
                    "skipped_route_audit",
                    (payload.get("route_audit") or {}),
                ),
            )

        from app.services.retrieval_routing import attach_retrieval_context, purpose_for_llm_node

        retrieval_ctx = attach_retrieval_context(state)
        state = merge_state(
            state,
            retrieval_decision=retrieval_ctx.get("retrieval_decision"),
            query_object=retrieval_ctx.get("query_object"),
            task_drift=retrieval_ctx.get("task_drift"),
        )
        _writing_purpose = purpose_for_llm_node(state, "writing")

        payload = dict(state.get("input_payload") or {})
        intent = dict(payload.get("writing_intent") or {})
        payload["retrieval_purpose"] = _writing_purpose

        from app.config.settings import settings

        if intent.get("enabled"):
            from app.services.mission_oma.orchestrator import should_use_mission_oma

            if should_use_mission_oma(state) and getattr(
                settings, "MISSION_OMA_REQUIRE_FACT_BUNDLE", True
            ):
                bundle = payload.get("fact_bundle") or intent.get("fact_bundle") or {}
                if not str(bundle.get("fact_bundle_id") or payload.get("fact_bundle_id") or ""):
                    return merge_state(
                        state,
                        status=TaskStatus.FAILED.value,
                        errors=list(state.get("errors") or [])
                        + ["OMAW: writing blocked without fact_bundle"],
                        current_node="writing",
                        audit_log=append_audit(
                            state,
                            "writing",
                            "blocked",
                            {"reason": "missing_fact_bundle"},
                        ),
                    )

        if not intent.get("enabled"):
            return merge_state(
                state,
                current_node="writing",
                audit_log=append_audit(state, "writing", "skipped", {"reason": "disabled"}),
            )

        action = str(intent.get("action") or "append_body")
        if action in PHASE_ACTIONS:
            report_progress(f"Writing 阶段：{action}…")
            report_status_trace("writing", f"写作阶段 action={action}")
            updated = run_writing_phase(state, intent)
            get_state_store().save(updated)
            return updated

        report_progress("Writing 节点：生成并校验正文…")
        report_status_trace("writing", f"开始写作 action={action}")
        task_id = state["task_id"]
        goal = str(payload.get("goal") or "")
        ms = resolve_manuscript(task_id, state.get("manuscript"))
        outline_file = resolve_outline_filename(manuscript=ms, payload=payload, intent=intent)
        body_file = resolve_body_filename(manuscript=ms, payload=payload, intent=intent)
        target_chars = int(intent.get("target_chars") or settings.ARTIFACT_CHUNK_CHARS)

        results: list[dict[str, Any]] = list(state.get("tool_results") or [])
        chapter_quality_metrics: dict[str, Any] = {}

        work_item_id = str(
            intent.get("work_item_id")
            or (payload.get("current_work_item") or {}).get("id")
            or f"wi-{state.get('mission_step')}"
        )
        from app.services.confirmation.snapshot import save_artifact_snapshot
        from app.services.confirmation.writing_delta import (
            finalize_writing_step_delta,
            persist_writing_delta,
            record_writing_step_start,
        )

        writing_delta: dict[str, Any] | None = None
        if action in ("append_body", "write_body", "write_outline", "reset_body"):
            delta_file = outline_file if action == "write_outline" else body_file
            writing_delta = record_writing_step_start(
                state,
                filename=delta_file,
                action=action,
                work_item_id=work_item_id,
            )

        if intent.get("require_read_first"):
            read_name = outline_file if action == "write_outline" else body_file
            if read_name:
                read_out = handle_read_text_artifact(
                    {"task_id": task_id, "filename": read_name, "max_chars": 8000}
                )
                results.append(
                    {"tool": "read_text_artifact", "status": "ok", "result": read_out}
                )
                excerpt = str(read_out.get("content") or "")[:4000]
                if action == "write_outline":
                    payload["existing_outline_excerpt"] = excerpt
                else:
                    payload["existing_body_excerpt"] = excerpt

        if action == "reset_body":
            filename = body_file
            save_artifact_snapshot(task_id, filename, work_item_id, state=state)
            payload["last_snapshot_id"] = work_item_id
            art_dir = task_artifact_dir(task_id)
            src = art_dir / filename
            if src.exists() and src.stat().st_size > 0:
                rev = int(ms.revision or 0) + 1
                archive = art_dir / f"novel_rev{rev}.txt"
                shutil.copy2(src, archive)
                src.write_text("", encoding="utf-8")
                ms.revision = rev
            ms.body_bytes = 0
            ms.chapter_cursor = 0
            ms.last_chapter_index = 0
            ms.body_revision = int(ms.body_revision or 0) + 1
            intent = {**intent, "action": "write_body", "chapter_index": 1}
            action = "write_body"

        if action == "write_outline":
            filename = outline_file
            save_artifact_snapshot(task_id, filename, work_item_id, state=state)
            payload["last_snapshot_id"] = work_item_id
            content = _generate_validated_content(
                state=state,
                tool_name="write_text_artifact",
                filename=filename,
                goal=goal,
                intent={**intent, "action": "write_outline"},
                target_chars=target_chars,
            )
            step_id = f"{work_item_id}_write_outline"
            committer = StepCommitter(
                merge_state(state, input_payload=payload),
                step_id=step_id,
                kind="write_outline",
                filename=filename,
                work_item_id=work_item_id,
                tool_name="write_text_artifact",
                min_chars=int(intent.get("min_chars") or settings.MANUSCRIPT_MIN_BODY_CHARS),
            )
            commit_state, commit_result = committer.commit(content)
            outcome = commit_result.artifact_outcome
            results.append({"tool": "write_text_artifact", "status": "ok", "result": outcome})
            ms = resolve_manuscript(task_id, commit_state.get("manuscript"))
            ms.outline_revision = int(ms.outline_revision or 0) + 1
            state = merge_state(commit_state, tool_results=results)
            payload = dict(state.get("input_payload") or {})
            payload["last_written_outline_excerpt"] = content[:5000]
            if intent.get("source") == "writing_command":
                ms.revision = int(ms.revision or 0) + 1

            # Outline rewrite — structured diff + multi-level alignment (replan pipeline).
            try:
                if manuscript_has_body(ms, min_bytes=256):
                    outline_before = str(payload.get("existing_outline_excerpt") or "").strip()
                    outline_after = str(payload.get("last_written_outline_excerpt") or "").strip()
                    if outline_before and outline_after and outline_before != outline_after:
                        from app.services.artifact_tools import read_artifact_tail
                        from app.services.mission_intervention import apply_intervention_to_payload
                        from app.services.outline_body_alignment import (
                            decide_outline_body_alignment,
                        )
                        from app.services.outline_diff import compute_outline_diff
                        from app.services.writing_reconcile import (
                            apply_bridge,
                            apply_chapter_patches,
                        )

                        body_name = ms.body_path or body_file
                        body_tail = read_artifact_tail(task_id, body_name, max_chars=2400)
                        diff = compute_outline_diff(
                            outline_before,
                            outline_after,
                            use_llm=False,
                        )
                        payload["outline_diff"] = diff.to_dict()
                        decision = decide_outline_body_alignment(
                            outline_before_excerpt=outline_before,
                            outline_after_excerpt=outline_after,
                            body_tail_excerpt=body_tail,
                            body_total_chars=int(ms.body_bytes or 0),
                            last_chapter_index=int(ms.last_chapter_index or 0),
                            user_goal=str(payload.get("goal") or ""),
                            outline_diff=diff,
                            trace_state=state,
                        )
                        payload["outline_body_alignment"] = decision.to_dict()
                        reconcile_state = merge_state(
                            state,
                            input_payload=payload,
                            manuscript=ms.to_dict(),
                            tool_results=results,
                        )
                        if decision.body_action == "rewrite_body":
                            payload = apply_intervention_to_payload(
                                payload,
                                {
                                    "action": "reset_body",
                                    "force": True,
                                    "reason": f"outline changed ({decision.change_level}): {decision.reason}",
                                },
                            )
                        elif decision.body_action == "append_with_bridge" and decision.bridge_spec:
                            reconcile_state, bridge_tools = apply_bridge(
                                reconcile_state,
                                spec=decision.bridge_spec,
                                decision=decision,
                            )
                            results.extend(bridge_tools)
                            ms = resolve_manuscript(task_id, reconcile_state.get("manuscript"))
                        elif (
                            decision.body_action == "patch_recent_chapters"
                            and decision.patch_instructions
                        ):
                            reconcile_state, patch_tools = apply_chapter_patches(
                                reconcile_state,
                                instructions=decision.patch_instructions,
                                decision=decision,
                            )
                            results.extend(patch_tools)
                            ms = resolve_manuscript(task_id, reconcile_state.get("manuscript"))
                        payload = reconcile_state.get("input_payload") or payload
            except Exception:
                # Alignment check is best-effort; writing must succeed even if it fails.
                pass

        elif action == "write_body":
            filename = body_file
            existing_body = read_body_text(task_id, filename, state=state)
            min_body = int(intent.get("min_chars") or settings.MANUSCRIPT_MIN_BODY_CHARS)
            if existing_body.strip() and len(existing_body) >= min_body:
                report_status_trace(
                    "writing",
                    f"{filename} 已有正文（{len(existing_body)} 字），改为 append 续写",
                )
                action = "append_body"
                intent = {**intent, "action": "append_body"}
                payload["writing_intent"] = intent
            else:
                content = _generate_validated_content(
                    state=state,
                    tool_name="write_text_artifact",
                    filename=filename,
                    goal=goal,
                    intent={**intent, "action": "write_body"},
                    target_chars=target_chars,
                )
                step_id = f"{work_item_id}_write_body"
                committer = StepCommitter(
                    merge_state(state, input_payload=payload),
                    step_id=step_id,
                    kind="write_body",
                    filename=filename,
                    work_item_id=work_item_id,
                    tool_name="write_text_artifact",
                    min_chars=min_body,
                )
                commit_state, commit_result = committer.commit(content)
                outcome = commit_result.artifact_outcome
                results.append({"tool": "write_text_artifact", "status": "ok", "result": outcome})
                ms = resolve_manuscript(task_id, commit_state.get("manuscript"))
                ms.body_path = filename
                ms.body_bytes = int(outcome.get("bytes") or 0)
                ms.body_outline_revision_seen = int(ms.outline_revision or 0)
                state = merge_state(commit_state, tool_results=results)
                payload = dict(state.get("input_payload") or {})

        if action == "append_body":
            filename = ms.body_path or body_file
            requested = parse_requested_chars(goal) or target_chars
            if requested > settings.ARTIFACT_CHUNK_CHARS:
                chunks = _build_append_chunks(state, goal, filename)
            else:
                content = _generate_validated_content(
                    state=state,
                    tool_name="append_text_artifact",
                    filename=filename,
                    goal=goal,
                    intent=intent,
                    target_chars=target_chars,
                )
                chunks = [content]

            from app.services.mission_steer import has_pending_steer

            for idx, chunk in enumerate(chunks):
                from app.services.execution_control import check_for_control_signal

                try:
                    check_for_control_signal(
                        task_id,
                        phase="append_chunk",
                        raise_on_pause=True,
                        raise_on_cancel=True,
                    )
                except (PauseRequested, CancelRequested):
                    report_status_trace(
                        "writing",
                        "检测到任务控制停止，本步写作提前结束（已写入部分将保留）",
                    )
                    payload["writing_stopped_for_steer"] = True
                    break
                if has_pending_steer(task_id):
                    report_status_trace(
                        "writing",
                        "检测到用户介入排队，本步写作提前结束（已写入部分将保留）",
                    )
                    payload["writing_stopped_for_steer"] = True
                    break
                report_progress(
                    f"Writing：追加 {filename} 第 {idx + 1}/{len(chunks)} 段…"
                )
                existing = read_body_text(task_id, filename, state=state)
                if existing.strip():
                    dup, ratio = is_near_duplicate_append(
                        existing,
                        chunk,
                        threshold=float(
                            getattr(settings, "MANUSCRIPT_APPEND_DEDUP_RATIO", 0.82)
                        ),
                    )
                    if dup:
                        raise ValueError(
                            f"Append rejected: near-duplicate of tail (ratio={ratio:.2f})"
                        )
                chunk_step_id = f"{work_item_id}_append_{idx}"
                chunk_state = merge_state(
                    state,
                    input_payload=payload,
                    tool_results=results,
                )
                chunk_committer = StepCommitter(
                    chunk_state,
                    step_id=chunk_step_id,
                    kind="append_body",
                    filename=filename,
                    work_item_id=work_item_id,
                    tool_name="append_text_artifact",
                )
                chunk_state, commit_result = chunk_committer.commit(chunk, partial=True)
                outcome = commit_result.artifact_outcome
                results.append(
                    {"tool": "append_text_artifact", "status": "ok", "result": outcome}
                )
                state = merge_state(chunk_state, tool_results=results)
                payload = dict(state.get("input_payload") or {})
            ms.body_path = filename
            ms = resolve_manuscript(task_id, ms.to_dict())
            ms.body_outline_revision_seen = int(ms.outline_revision or 0)

        bundle = payload.get("fact_bundle") or intent.get("fact_bundle") or {}
        if isinstance(bundle, dict) and bundle.get("evidence_text"):
            payload["oma_fact_evidence"] = str(bundle["evidence_text"])[:8000]

        if ms.body_path and action in ("append_body", "write_body"):
            body_text = read_body_text(task_id, ms.body_path, state=state)
            wctx = build_writing_context(
                task_id=task_id,
                state={**state, "input_payload": payload},
                body_filename=ms.body_path,
                chapter_index=intent.get("chapter_index"),
            )
            ms_dict = ms.to_dict()
            ms_dict, intent = sync_chapter_fields(
                manuscript=ms_dict,
                writing_intent=intent,
                last_chapter=wctx["last_written_chapter"],
                next_chapter=wctx["chapter_index"],
            )
            payload["writing_intent"] = intent
            ms = resolve_manuscript(task_id, ms_dict)

            try:
                ch_idx = int(wctx.get("last_written_chapter") or wctx.get("chapter_index") or 0)
                if ch_idx > 0 and action == "append_body":
                    from app.services.chapter_outcome import (
                        extract_chapter_outcome,
                        sync_story_bible_from_outcome,
                    )
                    from app.services.writing_phases import load_story_bible

                    outline_name = ms.outline_path or outline_file
                    outline_full = read_outline_text(task_id, outline_name, state=state)
                    chapter_text = extract_chapter_text(body_text, ch_idx)
                    prev_text = (
                        extract_chapter_text(body_text, ch_idx - 1) if ch_idx > 1 else ""
                    )
                    outline_slice = extract_outline_chapter_brief(outline_full, ch_idx)
                    outcome = extract_chapter_outcome(
                        task_id=task_id,
                        chapter_index=ch_idx,
                        chapter_text=chapter_text,
                        outline_slice=outline_slice,
                        prev_chapter_text=prev_text,
                        story_bible=load_story_bible(task_id),
                        persist=True,
                    )
                    sync_story_bible_from_outcome(task_id, outcome)
                    from app.services.writing_knowledge_index import (
                        upsert_chapter_facts_for_outcome,
                    )

                    upsert_chapter_facts_for_outcome(task_id, outcome)
                    payload["last_chapter_outcome"] = outcome.to_dict()
                    if outcome.quality_rubric:
                        chapter_quality_metrics = outcome.quality_rubric.to_dict()
            except Exception:
                pass

        if action == "write_outline":
            done_path = ms.outline_path
            done_bytes = ms.outline_bytes
        else:
            done_path = ms.body_path
            done_bytes = ms.body_bytes
        report_block(
            "writing",
            "done",
            f"【Writing 完成】{done_path} → {done_bytes} B",
            field="writing_result",
        )

        from app.services.writing_delivery import (
            clear_reasoning_regen_after_persist,
            should_force_slow_reasoning_after_write,
            writing_tool_results_ok,
        )

        audit = payload.get("route_audit") or {}
        command = payload.get("writing_command") or {}
        material_command = isinstance(command, dict) and command.get("action") in (
            "edit_plot",
            "reset_body",
            "write_outline",
        )
        if writing_tool_results_ok(results):
            payload = clear_reasoning_regen_after_persist(payload)
            audit = payload.get("route_audit") or {}
        force_reasoning = should_force_slow_reasoning_after_write(
            payload,
            audit if isinstance(audit, dict) else {},
            tool_results=results,
            material_command=material_command,
        )
        payload["skip_reasoning_after_tools"] = not force_reasoning

        progress_patch: dict[str, Any] = dict(state.get("progress") or {})
        if chapter_quality_metrics:
            metrics = dict(progress_patch.get("metrics") or {})
            metrics["chapter_quality"] = chapter_quality_metrics
            progress_patch["metrics"] = metrics
        if writing_delta is not None:
            delta_state = merge_state(
                state,
                input_payload=payload,
                manuscript=resolve_manuscript(task_id, ms.to_dict()).to_dict(),
            )
            writing_delta = finalize_writing_step_delta(delta_state, writing_delta)
            progress_patch = persist_writing_delta(
                {**state, "progress": progress_patch},
                writing_delta,
            )

        updated = merge_state(
            state,
            input_payload=payload,
            tool_results=results,
            progress=progress_patch,
            manuscript=resolve_manuscript(task_id, ms.to_dict()).to_dict(),
            status=TaskStatus.WRITTEN.value,
            current_node="writing",
            audit_log=append_audit(
                state,
                "writing",
                "success",
                {
                    "action": action,
                    "body_path": ms.body_path,
                    "body_bytes": ms.body_bytes,
                    "outline_path": ms.outline_path,
                    "outline_bytes": ms.outline_bytes,
                    "chunks": len(
                        [r for r in results if r.get("tool") == "append_text_artifact"]
                    ),
                },
            ),
        )
        updated = attach_turn_facts(updated)
        artifact_path = ms.body_path or ms.outline_path or action
        updated = record_turn_event(
            updated,
            "artifact_written",
            str(artifact_path or action),
            "writing",
            {
                "action": action,
                "body_bytes": ms.body_bytes,
                "outline_bytes": ms.outline_bytes,
            },
        )
        get_state_store().save(updated)
        return updated
    except Exception as exc:
        from app.services.foreground_execution import EpochStale

        if isinstance(exc, (SteerPreempted, EpochStale, PauseRequested, CancelRequested)):
            from app.services.mission_execution import (
                PAUSE_USER_REQUESTED_CANCEL,
                PAUSE_USER_REQUESTED_PAUSE,
            )

            payload = dict(state.get("input_payload") or {})
            payload["writing_stopped_for_steer"] = True
            paused = isinstance(exc, (SteerPreempted, EpochStale, PauseRequested))
            status = (
                TaskStatus.MISSION_PAUSED.value
                if paused
                else TaskStatus.CANCELLED.value
            )
            pause_reason = (
                PAUSE_USER_REQUESTED_PAUSE if paused else PAUSE_USER_REQUESTED_CANCEL
            )
            return merge_state(
                state,
                input_payload=payload,
                status=status,
                current_node="writing",
                mission_control={"pause_reason": pause_reason, "reason": str(exc)},
                audit_log=append_audit(
                    state,
                    "writing",
                    "preempted",
                    {"detail": str(exc)},
                ),
            )
        report_block("writing", "error", f"【Writing 失败】{exc}", field="writing_error")
        return merge_state(
            state,
            errors=list(state.get("errors", [])) + [f"writing: {exc}"],
            retry_count=state.get("retry_count", 0) + 1,
            status=TaskStatus.WRITING_FAILED.value,
            current_node="writing",
            audit_log=append_audit(state, "writing", "error", {"detail": str(exc)}),
        )
