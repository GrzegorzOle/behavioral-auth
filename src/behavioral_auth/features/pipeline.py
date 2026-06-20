"""Feature extraction pipeline.

Transforms raw input events stored in DuckDB into:
  1. feature_windows  – fixed-duration windows of 21 behavioural features
  2. fused_sequences  – sliding windows of seq_len consecutive feature vectors
                        (the format consumed by the ONNX autoencoder)

Feature columns (21 total):
  Keystroke (8): count, mean/std dwell, mean/std flight, backspace ratio,
                 repeat ratio, dwell entropy
  Mouse     (9): count, speed mean/std, acceleration mean, click count,
                 click dwell, scroll count, idle ratio, path curvature
  Context   (4): hour sin/cos, is_weekend flag, activity density
"""

from pathlib import Path
import json
import duckdb
import numpy as np
from loguru import logger
from behavioral_auth.config import load_settings
from behavioral_auth.features.keystroke import extract_keystroke_features
from behavioral_auth.features.mouse import extract_mouse_features
from behavioral_auth.features.context import extract_context_features

FEATURE_COLUMNS = [
    'f_ks_count', 'f_ks_mean_dwell', 'f_ks_std_dwell',
    'f_ks_mean_flight', 'f_ks_std_flight', 'f_ks_backspace_ratio',
    'f_ks_repeat_ratio', 'f_ks_entropy',
    'f_ms_count', 'f_ms_speed_mean', 'f_ms_speed_std', 'f_ms_acc_mean',
    'f_ms_clicks', 'f_ms_click_dwell', 'f_ms_scrolls', 'f_ms_idle_ratio',
    'f_ms_curvature',
    'f_ctx_hour_sin', 'f_ctx_hour_cos', 'f_ctx_is_weekend',
    'f_activity_density',
]


def load_session_events(conn, session_id):
    """Load all raw events for *session_id*, ordered by timestamp (ns)."""
    return conn.execute(
        'SELECT ts_ns, ts_utc, dev_type, ev_type, ev_code, ev_value '
        'FROM raw_events WHERE session_id = ? ORDER BY ts_ns',
        [session_id]
    ).fetchdf()


def build_feature_windows(conn, session_id, cfg) -> int:
    """Slide a fixed-duration window over *session_id* events and insert rows
    into *feature_windows*.  Returns the number of windows inserted."""
    df = load_session_events(conn, session_id)
    if df.empty:
        return 0
    win_ns    = int(cfg.features.window_sec * 1e9)
    stride_ns = int(cfg.features.stride_sec * 1e9)
    start = int(df.ts_ns.min())
    end   = int(df.ts_ns.max())
    inserted  = 0
    w = start
    while w + win_ns <= end:
        sub = df[(df.ts_ns >= w) & (df.ts_ns < w + win_ns)].copy()
        if sub.empty:
            w += stride_ns
            continue
        kf = extract_keystroke_features(sub[sub.dev_type == 'keyboard']) or {}
        mf = extract_mouse_features(sub) or {}
        # Skip windows with insufficient activity on both channels
        kb_count = len(sub[sub.dev_type == 'keyboard'])
        ms_count = len(sub[sub.dev_type == 'mouse'])
        if kb_count < cfg.features.min_keyboard_events and ms_count < cfg.features.min_mouse_events:
            w += stride_ns
            continue
        cf   = extract_context_features(sub.ts_utc, len(sub), cfg.features.window_sec)
        feats = {c: 0.0 for c in FEATURE_COLUMNS}
        feats.update(kf); feats.update(mf); feats.update(cf)
        cols         = ','.join(['session_id', 'window_start_ns', 'window_end_ns', 'source'] + FEATURE_COLUMNS)
        vals         = [session_id, w, w + win_ns, 'fused'] + [feats[c] for c in FEATURE_COLUMNS]
        placeholders = ','.join(['?'] * len(vals))
        conn.execute(f'INSERT INTO feature_windows ({cols}) VALUES ({placeholders})', vals)
        inserted += 1
        w += stride_ns
    return inserted


def build_sequences(conn, session_id, cfg) -> int:
    """Build sliding sequences of length *seq_len* from feature windows and
    insert them into *fused_sequences* (deduplication via dedup_key).
    Returns the number of unique sequences produced."""
    df = conn.execute(
        'SELECT * FROM feature_windows WHERE session_id = ? AND source = ? ORDER BY window_end_ns',
        [session_id, 'fused']
    ).fetchdf()
    if df.empty or len(df) < cfg.model.seq_len:
        return 0
    dedup_gap_ns = int(cfg.features.dedup_gap_sec * 1e9)
    inserted = 0
    for i in range(cfg.model.seq_len - 1, len(df)):
        seq     = df.iloc[i - cfg.model.seq_len + 1:i + 1][FEATURE_COLUMNS].fillna(0.0).to_numpy(dtype=float).tolist()
        seq_end = int(df.iloc[i].window_end_ns)
        dedup_key = seq_end // dedup_gap_ns
        try:
            conn.execute(
                'INSERT OR IGNORE INTO fused_sequences '
                '(session_id, seq_end_ns, seq_len, data_json, dedup_key) VALUES (?, ?, ?, ?, ?)',
                [session_id, seq_end, cfg.model.seq_len, json.dumps(seq), dedup_key]
            )
            inserted += 1
        except Exception:
            pass
    return max(0, len(df) - cfg.model.seq_len + 1)


def run_pipeline(session_filter: str | None = None) -> None:
    """Run the full feature extraction pipeline.

    Args:
        session_filter: If given, process only this session UUID.
                        Otherwise processes all sessions that have raw events.
    """
    cfg  = load_settings()
    conn = duckdb.connect(cfg.storage.db_path)
    if session_filter:
        sessions = [(session_filter,)]
    else:
        sessions = conn.execute(
            'SELECT DISTINCT session_id FROM raw_events ORDER BY session_id'
        ).fetchall()
    if not sessions:
        print('No sessions with raw events found. Run: behavioral-collector')
        return
    total_windows = 0
    total_seqs    = 0
    for (sid,) in sessions:
        n_raw = conn.execute(
            'SELECT COUNT(*) FROM raw_events WHERE session_id = ?', [sid]
        ).fetchone()[0]
        logger.info(f'Session {str(sid)[:8]}…  raw_events={n_raw:,}')
        w = build_feature_windows(conn, str(sid), cfg)
        s = build_sequences(conn, str(sid), cfg)
        logger.info(f'  → windows={w}  sequences={s}')
        total_windows += w
        total_seqs    += s
    n_total_seq = conn.execute('SELECT COUNT(*) FROM fused_sequences').fetchone()[0]
    print(f'Done: {total_windows} new windows, {total_seqs} new sequences')
    print(f'Total sequences in DB: {n_total_seq}')
    needed = cfg.model.seq_len * 5
    if n_total_seq < needed:
        print(f'⚠️  Not enough sequences ({n_total_seq}/{needed}). Collect more data and re-run.')
    else:
        print(f'✅  Sufficient data. You can now run: behavioral-train')
