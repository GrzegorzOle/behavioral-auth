"""DuckDB access.

DuckDB allows exactly one process to hold the database open for writing. The
daemon therefore opens it once and shares that single instance; anything that
needs to touch the DB from another thread takes a `conn.cursor()` off it,
which is a child connection on the same instance and is safe to use
concurrently. Never open a second `duckdb.connect()` on the same file, and
never pass `read_only` — a differing configuration makes DuckDB refuse to
reuse the cached instance, which is exactly the failure the old code hit.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from behavioral_auth.config import Settings, load_settings
from behavioral_auth.db.migrate import ensure_schema

__all__ = ['open_db', 'ensure_schema']


def open_db(cfg: Settings | None = None, *, migrate: bool = True) -> duckdb.DuckDBPyConnection:
    """Open (creating if needed) the configured database and apply migrations.

    This is the only place the database file is opened.
    """
    cfg = cfg or load_settings()
    path = Path(cfg.storage.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect(str(path))
    conn.execute('PRAGMA threads=4')
    if migrate:
        ensure_schema(conn)
    return conn
