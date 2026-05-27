from unittest.mock import MagicMock, patch

import pytest

from app.services.db import create_tenant_schema, drop_tenant_schema


def test_create_tenant_schema_executes_ddl(monkeypatch):
    monkeypatch.setattr("app.services.db.uses_postgres", lambda: True)
    conn = MagicMock()
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value = conn
    with patch("app.services.db.get_postgres_pool", return_value=pool):
        schema = create_tenant_schema("acme")
    assert schema == "tenant_acme"
    assert conn.execute.call_count >= 3
    first_sql = conn.execute.call_args_list[0][0][0]
    assert "CREATE SCHEMA IF NOT EXISTS tenant_acme" in first_sql


def test_drop_tenant_schema(monkeypatch):
    monkeypatch.setattr("app.services.db.uses_postgres", lambda: True)
    conn = MagicMock()
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value = conn
    with patch("app.services.db.get_postgres_pool", return_value=pool):
        schema = drop_tenant_schema("acme")
    assert schema == "tenant_acme"
    conn.execute.assert_called_once_with("DROP SCHEMA IF EXISTS tenant_acme CASCADE")


def test_create_tenant_schema_requires_postgres(monkeypatch):
    monkeypatch.setattr("app.services.db.uses_postgres", lambda: False)
    with pytest.raises(RuntimeError, match="postgres"):
        create_tenant_schema("acme")
