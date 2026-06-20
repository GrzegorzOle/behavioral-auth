"""Reporting module: print decision distribution and proxy FAR/FRR metrics.

Reads the *decisions* table from DuckDB and summarises:
  - Mean behavioural and fused scores
  - Decision value counts (ALLOW / CHALLENGE / LOCK)
  - FAR proxy : fraction of decisions at or above lock_threshold
  - FRR proxy : fraction of decisions at or below challenge_threshold
  - EER proxy : arithmetic mean of FAR and FRR proxies
"""

from pathlib import Path
import json
import duckdb
from behavioral_auth.config import load_settings


def report() -> None:
    """Print a metrics summary to stdout."""
    cfg = load_settings()
    conn = duckdb.connect(cfg.storage.db_path)
    df = conn.execute(
        'SELECT ts_utc, behavioral_score, howdy_score, fused_score, decision, mode '
        'FROM decisions ORDER BY ts_utc'
    ).fetchdf()
    if df.empty:
        print('No decisions recorded yet.')
        return
    meta_path = Path(cfg.model.metadata_path)
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    print('rows=', len(df))
    print('behavioral_mean=', float(df.behavioral_score.mean()))
    print('fused_mean=', float(df.fused_score.mean()))
    print(df.decision.value_counts().to_string())
    if meta:
        far = float((df.fused_score >= meta.get('lock_threshold', 1.0)).mean())
        frr = float((df.fused_score <= meta.get('challenge_threshold', 0.0)).mean())
        print(json.dumps(
            {'FAR_proxy': far, 'FRR_proxy': frr, 'EER_proxy': (far + frr) / 2.0},
            indent=2,
        ))
