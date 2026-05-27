from app.domain.packs.registry import (
    build_supervisor_decompose_system_prompt,
    build_worker_catalog,
    get_domain_pack,
    list_domain_packs,
)
from app.domain.worker_executor import decompose_task


def test_list_domain_packs():
    packs = list_domain_packs()
    names = {pack.name for pack in packs}
    assert "document" in names
    assert "code" in names


def test_worker_catalog_for_supervisor_prompt():
    catalog = build_worker_catalog(["document", "code"])
    names = {item["domain"] for item in catalog}
    assert names == {"document", "code"}
    prompt = build_supervisor_decompose_system_prompt(["document", "code", "analysis"])
    assert "document" in prompt
    assert "Worker catalog" in prompt


def test_decompose_task_fallback_single_worker():
    subtasks = decompose_task("analyze quarterly report", domains=["document", "analysis"])
    assert len(subtasks) == 1
    assert subtasks[0]["domain"] in ("document", "analysis")
    assert subtasks[0]["description"]


def test_get_domain_pack():
    pack = get_domain_pack("document")
    assert pack.name == "document"
