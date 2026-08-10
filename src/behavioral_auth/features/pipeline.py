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

from behavioral_auth.collector.stack import stack_key
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
        'SELECT ts_ns, ts_utc, dev_id, dev_type, ev_type, ev_code, ev_value, '
        'kb_zone, kb_pair '
        'FROM raw_events WHERE session_id = ? AND ts_ns >= ? ORDER BY ts_ns',
        [session_id, since_ns],
    ).fetchdf()


def _ids(rows) -> set[str]:
    """Distinct non-null device ids in *rows*.

    Events written before the stack migration have no id. They are read as an
    absent device rather than as a mismatch, so upgrading does not retroactively
    invalidate a pattern — the gate only starts biting once real ids flow.
    """
    if rows.empty or 'dev_id' not in rows:
        return set()
    return {v for v in rows.dev_id.dropna().unique() if v}


def window_stack(kb, ms) -> str | None:
    """The stack a window came from, or None if it straddles a change.

    A window containing two different keyboards spans the moment a dock was
    attached or dropped. It is a transition, not a person, and is discarded the
    same way build_sequences discards a sequence straddling an idle gap.
    """
    kb_ids, ms_ids = _ids(kb), _ids(ms)
    if len(kb_ids) > 1 or len(ms_ids) > 1:
        return None
    return stack_key(next(iter(kb_ids), None), next(iter(ms_ids), None))


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

        fp = window_stack(kb, ms)
        if fp is None:
            w += stride_ns          # straddles a hardware change; not a person
            continue

        ks_feats = extract_keystroke_features(kb)
        ms_feats = extract_mouse_features(sub)
        # Passing the event-count check above is not the same as the extractors
        # having something to say: a window can hold hundreds of mouse rows and
        # still yield nothing extractable. Storing it anyway wrote a row of
        # zeros that is indistinguishable from "the user sat still" — and those
        # rows are counted as activity, trained on, and used to build the
        # synthetic impostors that guard promotion. An absent window is honest;
        # a zero-filled one is a lie the rest of the pipeline believes.
        if ks_feats is None and ms_feats is None:
            w += stride_ns
            continue

        feats = {c: 0.0 for c in FEATURE_COLUMNS}
        feats.update(ks_feats or {})
        feats.update(ms_feats or {})
        feats.update(extract_context_features(sub.ts_utc, len(sub), cfg.features.window_sec))

        cols = ['session_id', 'enrollment_id', 'window_start_ns', 'window_end_ns',
                'source', 'stack_fp']
        vals = [session_id, enrollment_id, w, w + win_ns, 'fused', fp]
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

    It is rejected for the same reason when its windows do not all come from one
    hardware stack. A sequence spanning the moment a dock was attached mixes two
    motor contexts into one training sample, and the mixture is what makes a
    pattern permissive.
    """
    seq_len = cfg.model.seq_len
    df = conn.execute(
        'SELECT window_start_ns, window_end_ns, stack_fp, '
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
    stacks = df.stack_fp.to_numpy(dtype=object)
    feats = df[FEATURE_COLUMNS].fillna(0.0).to_numpy(dtype=float)

    inserted = 0
    for i in range(seq_len - 1, len(df)):
        seq_end = int(df.window_end_ns.iloc[i])
        if seq_end <= watermark:
            continue

        lo = i - seq_len + 1
        if np.max(np.diff(starts[lo:i + 1])) > max_gap_ns:
            continue  # spans an idle gap
        if len(set(stacks[lo:i + 1])) > 1:
            continue  # spans a change of hardware

        try:
            res = conn.execute(
                'INSERT OR IGNORE INTO fused_sequences '
                '(session_id, enrollment_id, seq_end_ns, seq_len, data_json, '
                ' dedup_key, stack_fp) VALUES (?, ?, ?, ?, ?, ?, ?)',
                [session_id, enrollment_id, seq_end, seq_len,
                 json.dumps(feats[lo:i + 1].tolist()), seq_end // dedup_gap_ns,
                 stacks[i]],
            ).fetchone()
            inserted += int(res[0]) if res else 0
        except duckdb.Error as exc:
            logger.warning(f'Sequence insert failed at seq_end={seq_end}: {exc}')

    return inserted
