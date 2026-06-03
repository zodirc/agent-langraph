"""
Task agenda — dependency-aware work queue on top of work_plan.

Generalizes lazy/explicit work_plan items with depends_on, blocked/failed
propagation, and partial replan slices.
"""

from __future__ import annotations

from typing import Any, Optional

TERMINAL_STATUSES = frozenset({"done", "cancelled", "superseded"})
ACTIVE_STATUSES = frozenset({"pending", "running", "blocked", "failed"})


def ensure_agenda_fields(plan: dict[str, Any]) -> dict[str, Any]:
    """Normalize work_plan items with agenda metadata."""
    out = repair_work_plan_dependencies(dict(plan or {}))
    items: list[dict[str, Any]] = []
    for row in list(out.get("items") or []):
        if not isinstance(row, dict):
            continue
        item = dict(row)
        deps = item.get("depends_on")
        if deps is None:
            item["depends_on"] = []
        elif not isinstance(deps, list):
            item["depends_on"] = [str(deps)]
        else:
            item["depends_on"] = [str(d) for d in deps if str(d)]
        item["status"] = str(item.get("status") or "pending")
        items.append(item)
    out["items"] = items
    out.setdefault("agenda_version", int(out.get("agenda_version") or 1))
    return out


def repair_work_plan_dependencies(plan: dict[str, Any]) -> dict[str, Any]:
    """Remove self-dependencies that block lazy work_plan enqueue (wi-step-N on itself)."""
    items_in: list[dict[str, Any]] = [
        dict(row)
        for row in (plan.get("items") or [])
        if isinstance(row, dict)
    ]
    if not items_in:
        return dict(plan or {})
    repaired: list[dict[str, Any]] = []
    changed = False
    for row in items_in:
        item = dict(row)
        item_id = str(item.get("id") or "")
        raw_deps = item.get("depends_on")
        if raw_deps is None:
            deps = []
        elif not isinstance(raw_deps, list):
            deps = [str(raw_deps)]
        else:
            deps = [str(d) for d in raw_deps if str(d)]
        if item_id:
            cleaned = [d for d in deps if d != item_id]
            if len(cleaned) != len(deps):
                changed = True
            deps = cleaned
        if deps != item.get("depends_on"):
            changed = True
        item["depends_on"] = deps
        if (
            str(item.get("status") or "") == "blocked"
            and not deps
            and item_id
        ):
            item["status"] = "pending"
            changed = True
        repaired.append(item)
    if not changed:
        return dict(plan or {})
    out = dict(plan or {})
    out["items"] = repaired
    return out


def _items_by_id(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("id")): row
        for row in (plan.get("items") or [])
        if isinstance(row, dict) and row.get("id")
    }


def dependencies_met(item: dict[str, Any], plan: dict[str, Any]) -> bool:
    by_id = _items_by_id(plan)
    for dep_id in item.get("depends_on") or []:
        dep = by_id.get(str(dep_id))
        if not dep:
            continue
        if str(dep.get("status") or "") != "done":
            return False
    return True


def runnable_items(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Return pending items whose dependencies are satisfied."""
    plan = ensure_agenda_fields(plan)
    ready: list[dict[str, Any]] = []
    for row in plan.get("items") or []:
        if str(row.get("status") or "") != "pending":
            continue
        if dependencies_met(row, plan):
            ready.append(row)
    return ready


def select_next_runnable_item(plan: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Pick the first runnable item; prefer current_id if still runnable."""
    plan = ensure_agenda_fields(plan)
    running = [r for r in plan.get("items") or [] if str(r.get("status") or "") == "running"]
    if running:
        return running[0]

    current_id = plan.get("current_id")
    if current_id:
        by_id = _items_by_id(plan)
        current = by_id.get(str(current_id))
        if current and str(current.get("status") or "") == "pending" and dependencies_met(
            current, plan
        ):
            return current

    ready = runnable_items(plan)
    return ready[0] if ready else None


def set_item_status(
    plan: dict[str, Any],
    item_id: str,
    status: str,
    *,
    reason: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    plan = ensure_agenda_fields(plan)
    items = list(plan.get("items") or [])
    for idx, row in enumerate(items):
        if str(row.get("id")) == str(item_id):
            patch = {"status": status}
            if reason:
                patch["status_reason"] = reason
            if extra:
                patch.update(extra)
            items[idx] = {**row, **patch}
            break
    plan["items"] = items
    return plan


def propagate_failure(plan: dict[str, Any], failed_id: str) -> dict[str, Any]:
    """Mark dependents of a failed item as blocked."""
    plan = ensure_agenda_fields(plan)
    by_id = _items_by_id(plan)
    blocked: set[str] = set()

    def _dependents(target_id: str) -> list[str]:
        deps: list[str] = []
        for row in plan.get("items") or []:
            if target_id in [str(d) for d in (row.get("depends_on") or [])]:
                deps.append(str(row.get("id")))
        return deps

    queue = [str(failed_id)]
    while queue:
        current = queue.pop(0)
        for child_id in _dependents(current):
            if child_id in blocked:
                continue
            child = by_id.get(child_id)
            if not child:
                continue
            status = str(child.get("status") or "")
            if status in TERMINAL_STATUSES:
                continue
            plan = set_item_status(
                plan,
                child_id,
                "blocked",
                reason=f"dependency_failed:{current}",
            )
            blocked.add(child_id)
            queue.append(child_id)
    return plan


def items_from_plan_steps(
    steps: list[str],
    *,
    tool_dag: Optional[dict[str, Any]] = None,
    prefix: str = "agenda",
) -> list[dict[str, Any]]:
    """Build sequential agenda items from planning steps (optional DAG edges)."""
    items: list[dict[str, Any]] = []
    step_ids: list[str] = []
    for index, step in enumerate(steps or []):
        if not str(step).strip():
            continue
        item_id = f"{prefix}-{index + 1}"
        depends_on: list[str] = [step_ids[-1]] if step_ids else []
        items.append(
            {
                "id": item_id,
                "kind": "plan_step",
                "title": str(step).strip()[:200],
                "status": "pending",
                "depends_on": depends_on,
                "params": {"step_text": str(step).strip()},
            }
        )
        step_ids.append(item_id)

    if tool_dag and isinstance(tool_dag.get("nodes"), list):
        node_ids = {
            str(n.get("id") or n.get("tool")): str(n.get("tool") or n.get("id"))
            for n in tool_dag.get("nodes") or []
            if isinstance(n, dict)
        }
        edges = tool_dag.get("edges") or []
        if node_ids and edges:
            dag_items: list[dict[str, Any]] = []
            for nid, tool in node_ids.items():
                dag_items.append(
                    {
                        "id": f"tool-{nid}",
                        "kind": "tool_step",
                        "title": tool,
                        "status": "pending",
                        "depends_on": [],
                        "params": {"tool": tool},
                    }
                )
            by_dag_id = {str(n.get("id") or n.get("tool")): f"tool-{n.get('id') or n.get('tool')}" for n in tool_dag.get("nodes") or [] if isinstance(n, dict)}
            for edge in edges:
                if isinstance(edge, dict):
                    src, dst = str(edge.get("from")), str(edge.get("to"))
                elif isinstance(edge, (list, tuple)) and len(edge) >= 2:
                    src, dst = str(edge[0]), str(edge[1])
                else:
                    continue
                dst_item = next((i for i in dag_items if i["id"] == by_dag_id.get(dst)), None)
                src_item = by_dag_id.get(src)
                if dst_item and src_item:
                    dst_item["depends_on"] = list(dict.fromkeys(list(dst_item["depends_on"]) + [src_item]))
            items.extend(dag_items)
    return items


def merge_agenda_into_work_plan(
    plan: dict[str, Any],
    agenda_items: list[dict[str, Any]],
    *,
    mode: str = "agenda",
) -> dict[str, Any]:
    """Attach high-level agenda items without replacing lazy execution items."""
    plan = ensure_agenda_fields(plan)
    if not agenda_items:
        return plan
    existing_ids = {str(i.get("id")) for i in plan.get("items") or [] if i.get("id")}
    merged = list(plan.get("items") or [])
    for item in agenda_items:
        if str(item.get("id")) in existing_ids:
            continue
        merged.append(dict(item))
    plan["items"] = merged
    plan["total_items"] = len(merged)
    plan["mode"] = plan.get("mode") or mode
    plan["agenda_version"] = int(plan.get("agenda_version") or 1) + 1
    return plan


def local_replan_slice(plan: dict[str, Any], item_id: str) -> dict[str, Any]:
    """Reset failed/blocked slice rooted at item_id for partial replan."""
    plan = ensure_agenda_fields(plan)
    target = str(item_id)
    reset_ids = {target}

    def _collect_blocked(root: str) -> None:
        for row in plan.get("items") or []:
            rid = str(row.get("id") or "")
            if rid in reset_ids:
                continue
            deps = [str(d) for d in (row.get("depends_on") or [])]
            if deps and any(d in reset_ids for d in deps):
                if str(row.get("status") or "") in ("blocked", "failed", "pending"):
                    reset_ids.add(rid)
                    _collect_blocked(rid)

    _collect_blocked(target)
    for rid in reset_ids:
        plan = set_item_status(plan, rid, "pending", reason="local_replan")
    plan["current_id"] = None
    return plan


def agenda_summary(plan: dict[str, Any]) -> dict[str, Any]:
    plan = ensure_agenda_fields(plan)
    counts: dict[str, int] = {}
    for row in plan.get("items") or []:
        status = str(row.get("status") or "pending")
        counts[status] = counts.get(status, 0) + 1
    return {
        "total": len(plan.get("items") or []),
        "counts": counts,
        "runnable": len(runnable_items(plan)),
        "current_id": plan.get("current_id"),
        "agenda_version": plan.get("agenda_version"),
    }
