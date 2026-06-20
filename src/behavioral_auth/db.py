"""Convenience helper to open a DuckDB connection using the active config."""

from behavioral_auth.config import load_settings
import duckdb


def connect(read_only: bool = False):
    """Return a DuckDB connection to the configured database path.

    Args:
        read_only: Open in read-only mode (safe for concurrent reads).

    Returns:
        duckdb.DuckDBPyConnection
    """
    cfg = load_settings()
    return duckdb.connect(cfg.storage.db_path, read_only=read_only)
