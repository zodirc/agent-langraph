"""Tenant isolation: knowledge metadata + quota + SQLite state stores."""

from app.config.settings import settings
from app.runtime.state import create_initial_state
from app.services.knowledge_store import get_knowledge_store
from app.services.state_store import get_state_store
from app.services.tenant_context import set_tenant_id
from app.services.tenant_quota import check_quota, record_usage, reset_tenant_quota_store
from app.services.tenant_storage import create_tenant_storage, reset_tenant_store_caches


def test_knowledge_tenant_isolation(isolated_stores, test_settings, monkeypatch):
    monkeypatch.setattr(settings, "MULTI_TENANT_ENABLED", True)
    store = get_knowledge_store()

    set_tenant_id("alpha")
    store.upsert_document("Alpha Doc", "tenant alpha secret phrase", metadata={})

    set_tenant_id("beta")
    store.upsert_document("Beta Doc", "tenant beta other phrase", metadata={})

    set_tenant_id("alpha")
    hits = store.hybrid_search("alpha secret", top_k=5)
    assert hits
    assert all("alpha" in (h.get("content") or "").lower() for h in hits)
    assert not any("beta other" in (h.get("content") or "").lower() for h in hits)


def test_state_store_sqlite_isolation(tmp_path, monkeypatch):
    db = tmp_path / "db" / "agent.db"
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(settings, "POSTGRES_URL", "")
    monkeypatch.setattr(settings, "SQLITE_PATH", str(db))
    monkeypatch.setattr(settings, "VECTORSTORE_PATH", str(tmp_path / "vectorstore"))
    monkeypatch.setattr(settings, "MULTI_TENANT_ENABLED", True)
    reset_tenant_store_caches()

    create_tenant_storage("alpha")
    create_tenant_storage("beta")
    reset_tenant_store_caches()

    set_tenant_id("alpha")
    alpha_state = create_initial_state(user_id="u1", input_payload={"goal": "alpha task"})
    get_state_store().save(alpha_state)

    set_tenant_id("beta")
    beta_state = create_initial_state(user_id="u2", input_payload={"goal": "beta task"})
    get_state_store().save(beta_state)

    set_tenant_id("alpha")
    loaded = get_state_store().load(alpha_state["task_id"])
    assert loaded is not None
    assert loaded["input_payload"]["goal"] == "alpha task"

    set_tenant_id("beta")
    assert get_state_store().load(alpha_state["task_id"]) is None
    loaded_beta = get_state_store().load(beta_state["task_id"])
    assert loaded_beta is not None
    assert loaded_beta["input_payload"]["goal"] == "beta task"


def test_token_quota_does_not_block_other_tenant(monkeypatch):
    reset_tenant_quota_store()
    monkeypatch.setattr(settings, "MULTI_TENANT_ENABLED", True)
    monkeypatch.setattr(settings, "TENANT_MAX_TOKENS_PER_DAY", 100)
    record_usage("tenant_a", "tokens", 100)
    assert check_quota("tenant_a", "tokens", 1) is False
    assert check_quota("tenant_b", "tokens", 1) is True

