"""SQLite compatibility shim for ChromaDB (requires sqlite3 >= 3.35)."""

from __future__ import annotations

import sqlite3 as _stdlib_sqlite3

try:
    import pysqlite3 as _pysqlite3  # type: ignore

    sqlite3 = _pysqlite3
    SQLITE_SHIM = "pysqlite3"
except ImportError:
    sqlite3 = _stdlib_sqlite3
    SQLITE_SHIM = "stdlib"

sqlite3_version = sqlite3.sqlite_version
