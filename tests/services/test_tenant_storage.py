from pathlib import Path

import pytest

from app.config.settings import settings
from app.services.tenant_storage import (
    create_tenant_storage,
    drop_tenant_storage,
    sanitize_tenant_id,
    sqlite_path_for_tenant,
    tenant_cache_key,
    vectorstore_path_for_tenant,
)


def test_sanitize_tenant_id():
    assert sanitize_tenant_id("acme-corp_1") == "acme-corp_1"
    assert sanitize_tenant_id("bad/id!") == "badid"
    with pytest.raises(ValueError):
        sanitize_tenant_id("!!!")


def test_sqlite_paths(tmp_path, monkeypatch):
    db = tmp_path / "db" / "agent.db"
    monkeypatch.setattr(settings, "SQLITE_PATH", str(db))
    monkeypatch.setattr(settings, "VECTORSTORE_PATH", str(tmp_path / "vectorstore"))
    assert sqlite_path_for_tenant("acme") == str(tmp_path / "db" / "tenant_acme.db")
    assert vectorstore_path_for_tenant("acme") == str(tmp_path / "vectorstore" / "tenant_acme")


def test_tenant_cache_key_default(monkeypatch):
    monkeypatch.setattr(settings, "MULTI_TENANT_ENABLED", False)
    assert tenant_cache_key() == "default"


def test_create_and_drop_sqlite_tenant(tmp_path, monkeypatch):
    db = tmp_path / "db" / "agent.db"
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(settings, "POSTGRES_URL", "")
    monkeypatch.setattr(settings, "SQLITE_PATH", str(db))
    monkeypatch.setattr(settings, "VECTORSTORE_PATH", str(tmp_path / "vectorstore"))
    monkeypatch.setattr(settings, "MULTI_TENANT_ENABLED", True)

    info = create_tenant_storage("acme")
    assert info["backend"] == "sqlite"
    assert Path(info["db_path"]).exists()
    assert Path(info["vector_path"]).is_dir()

    drop_tenant_storage("acme")
    assert not Path(info["db_path"]).exists()
    assert not Path(info["vector_path"]).exists()
