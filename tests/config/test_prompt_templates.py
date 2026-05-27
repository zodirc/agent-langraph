from app.config.prompt_templates import (
    DOMAIN_PLANNING_OVERLAYS,
    list_template_catalog,
    resolve_system_prompt,
)
from app.runtime.state import create_initial_state, merge_state


def test_planning_overlay_writing_domain():
    state = merge_state(
        create_initial_state(),
        input_payload={"domain": "writing", "goal": "写小说"},
        mission={"kind": "writing"},
    )
    prompt = resolve_system_prompt("planning", state=state)
    assert "long-form writing" in prompt.lower() or "writing" in prompt.lower()


def test_reasoning_cot_mode():
    state = merge_state(
        create_initial_state(),
        input_payload={"reasoning_mode": "cot"},
    )
    prompt = resolve_system_prompt("reasoning", state=state)
    assert "chain-of-thought" in prompt.lower()


def test_template_catalog_lists_domains():
    catalog = list_template_catalog()
    planning = next(c for c in catalog if c["purpose"] == "planning")
    assert "writing" in planning["domains"]
    assert "writing" in DOMAIN_PLANNING_OVERLAYS
