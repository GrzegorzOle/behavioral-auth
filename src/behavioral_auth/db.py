from behavioral_auth.config import load_settings
import duckdb

def connect(read_only: bool = False):
    cfg = load_settings()
    return duckdb.connect(cfg.storage.db_path, read_only=read_only)
