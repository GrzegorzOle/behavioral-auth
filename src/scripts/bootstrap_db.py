#!/usr/bin/env python3
"""Initialize the DuckDB schema.  Reads config from the standard search path."""
from pathlib import Path
import sys, duckdb

# Allow running from repo root without install
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from behavioral_auth.config import load_settings

cfg = load_settings()
db  = cfg.storage.db_path

# Schema file: prefer project-local copy, fall back to installed location
_here = Path(__file__).resolve().parents[2]
for candidate in [
    _here / 'db' / 'schema.sql',
    Path('/etc/behavioral-auth/schema.sql'),
]:
    if candidate.exists():
        schema_path = candidate
        break
else:
    print('ERROR: schema.sql not found', file=sys.stderr)
    sys.exit(1)

schema = schema_path.read_text()
Path(db).parent.mkdir(parents=True, exist_ok=True)
conn = duckdb.connect(db)
conn.execute(schema)
conn.close()
print(f'Database initialised: {db}')
