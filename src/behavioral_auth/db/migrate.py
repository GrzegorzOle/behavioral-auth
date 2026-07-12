"""Schema migration runner.

Migrations are .sql files shipped inside this package, applied once each in
lexical order and recorded in schema_migrations. Running them again is a
no-op, so ensure_schema() is safe to call on every daemon start — a fresh
machine bootstraps itself with no manual step.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import duckdb
from loguru import logger

_MIGRATIONS_PKG = 'behavioral_auth.db.migrations'


def migration_files() -> list[Path]:
    """Return the packaged .sql migrations, sorted by filename."""
    root = resources.files(_MIGRATIONS_PKG)
    return sorted(
        (Path(str(p)) for p in root.iterdir() if p.name.endswith('.sql')),
        key=lambda p: p.name,
    )


def applied_versions(conn: duckdb.DuckDBPyConnection) -> set[str]:
    conn.execute(
        'CREATE TABLE IF NOT EXISTS schema_migrations ('
        '  version VARCHAR PRIMARY KEY,'
        '  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()'
        ')'
    )
    return {r[0] for r in conn.execute('SELECT version FROM schema_migrations').fetchall()}


def ensure_schema(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """Apply every migration not yet recorded. Returns the versions applied."""
    applied = applied_versions(conn)
    fresh: list[str] = []

    for path in migration_files():
        version = path.name
        if version in applied:
            continue
        logger.info(f'Applying migration {version}')
        conn.execute('BEGIN TRANSACTION')
        try:
            conn.execute(path.read_text())
            conn.execute('INSERT INTO schema_migrations (version) VALUES (?)', [version])
            conn.execute('COMMIT')
        except Exception:
            conn.execute('ROLLBACK')
            logger.error(f'Migration {version} failed — rolled back')
            raise
        fresh.append(version)

    return fresh
