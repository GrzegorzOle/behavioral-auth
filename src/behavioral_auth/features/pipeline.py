"""Feature extraction pipeline.

Turns raw input events into:
  1. feature_windows  – fixed-duration windows of 21 behavioural features
  2. fused_sequences  – sliding runs of seq_len consecutive windows, the
                        format the autoencoder consumes

Both steps are *incremental*: they resume from a watermark and only produce
what is new. The daemon calls them every tick, so re-deriving the whole
session each time would be quadratic — and, before the unique index existed,
it also duplicated every window on every run.
"""

from __future__ import annotations

import json

import duckdb
import numpy as np
from loguru import logger

from behavioral_auth.config import FEATURE_COLUMNS, MODEL_COL_IDX, Settings
from behavioral_auth.features.context import extract_context_features
from behavioral_auth.features.keystroke import extract_keystroke_features
from behavioral_auth.features.mouse import extract_mouse_features

__all__ = [
    'FEATURE_COLUMNS',
    'build_feature_windows',
    'build_sequences',
    'to_model_input',
]


def to_model_input(X: np.ndarray) -> np.ndarray:
    """Project stored 21-feature vectors onto the model's input columns."""
    return X[..., MODEL_COL_IDX]


def _load_events(conn, session_id: str, since_ns: int):
    """Events for *session_id* at or after *since_ns*, ordered by time."""
    return conn.execute(
        'SELECT ts_ns, ts_utc, dev_type, ev_type, ev_code, ev_value '
        'FROM raw_events WHERE session_id = ? AND ts_ns >= ? ORDER BY ts_ns',
        [session_id, since_ns],
    ).fetchdf()


def build_feature_windows(conn, session_id: str, enrollment_id: str, cfg: Settings) -> int:
    """Extract every window that has closed since the last call.

    Windows sit on a fixed grid anchored at the session's first event, so the
    same window boundaries are produced no matter when this runs. Returns the
    number of windows inserted.
    """
    bounds = conn.execute(
        'SELECT min(ts_ns), max(ts_ns) FROM raw_events WHERE session_id = ?',
        [session_id],
    ).fetchone()
    if not bounds or bounds[0] is None:
        return 0
    origin, last_event_ns = int(bounds[0]), int(bounds[1])

    win_ns = int(cfg.features.window_sec * 1e9)
    stride_ns = int(cfg.features.stride_sec * 1e9)

    watermark = conn.execute(
        'SELECT max(window_start_ns) FROM feature_windows WHERE session_id = ?',
        [session_id],
    ).fetchone()[0]
    # Resume one stride past the newest window we already have.
    start = origin if watermark is None else int(watermark) + stride_ns
    if start + win_ns > last_event_ns:
        return 0

    df = _load_events(conn, session_id, start)
    if df.empty:
        return 0

    inserted = 0
    w = start
    while w + win_ns <= last_event_ns:
        sub = df[(df.ts_ns >= w) & (df.ts_ns < w + win_ns)]
        if sub.empty:
            w += stride_ns
            continue

        kb = sub[sub.dev_type == 'keyboard']
        ms = sub[sub.dev_type == 'mouse']
        # A window with almost no activity on either channel says nothing
        # about who is typing; skipping it leaves a hole, which is why
        # build_sequences has to guard against idle gaps.
        if (len(kb) < cfg.features.min_keyboard_events
                and len(ms) < cfg.features.min_mouse_events):
            w += stride_ns
            continue

        feats = {c: 0.0 for c in FEATURE_COLUMNS}
        feats.update(extract_keystroke_features(kb) or {})
        feats.update(extract_mouse_features(sub) or {})
        feats.update(extract_context_features(sub.ts_utc, len(sub), cfg.features.window_sec))

        cols = ['session_id', 'enrollment_id', 'window_start_ns', 'window_end_ns', 'source']
        vals = [session_id, enrollment_id, w, w + win_ns, 'fused']
        vals += [float(feats[c]) for c in FEATURE_COLUMNS]
        placeholders = ','.join(['?'] * (len(cols) + len(FEATURE_COLUMNS)))
        res = conn.execute(
            f'INSERT OR IGNORE INTO feature_windows '
            f'({",".join(cols + FEATURE_COLUMNS)}) VALUES ({placeholders})',
            vals,
        ).fetchone()
        inserted += int(res[0]) if res else 0
        w += stride_ns

    return inserted


def build_sequences(conn, session_id: str, enrollment_id: str, cfg: Settings) -> int:
    """Assemble sliding sequences of seq_len windows. Returns rows inserted.

    A sequence is rejected when two adjacent windows are further apart than
    max_seq_gap_sec: windows below the activity threshold are dropped, so the
    rows are not time-contiguous, and without this guard a single sequence
    could splice Monday morning onto Tuesday evening.
    """
    seq_len = cfg.model.seq_len
    df = conn.execute(
        'SELECT window_start_ns, window_end_ns, '
        + ','.join(FEATURE_COLUMNS)
        + ' FROM feature_windows WHERE session_id = ? AND source = ? '
        'ORDER BY window_end_ns',
        [session_id, 'fused'],
    ).fetchdf()
    if len(df) < seq_len:
        return 0

    watermark = conn.execute(
        'SELECT max(seq_end_ns) FROM fused_sequences WHERE session_id = ?',
        [session_id],
    ).fetchone()[0]
    watermark = -1 if watermark is None else int(watermark)

    dedup_gap_ns = int(cfg.features.dedup_gap_sec * 1e9)
    max_gap_ns = int(cfg.features.max_seq_gap_sec * 1e9)
    starts = df.window_start_ns.to_numpy(dtype=np.int64)
    feats = df[FEATURE_COLUMNS].fillna(0.0).to_numpy(dtype=float)

    inserted = 0
    for i in range(seq_len - 1, len(df)):
        seq_end = int(df.window_end_ns.iloc[i])
        if seq_end <= watermark:
            continue

        lo = i - seq_len + 1
        if np.max(np.diff(starts[lo:i + 1])) > max_gap_ns:
            continue  # spans an idle gap

        try:
            res = conn.execute(
                'INSERT OR IGNORE INTO fused_sequences '
                '(session_id, enrollment_id, seq_end_ns, seq_len, data_json, dedup_key) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                [session_id, enrollment_id, seq_end, seq_len,
                 json.dumps(feats[lo:i + 1].tolist()), seq_end // dedup_gap_ns],
            ).fetchone()
            inserted += int(res[0]) if res else 0
        except duckdb.Error as exc:
            logger.warning(f'Sequence insert failed at seq_end={seq_end}: {exc}')

    return inserted
