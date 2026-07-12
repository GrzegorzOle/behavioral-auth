"""Training dataset loader.

Scoped to a single enrollment: a pattern must never be trained on sequences
belonging to a retired enrollment, or to a test/impostor session.
"""

from __future__ import annotations

import json

import numpy as np


def load_sequences(conn, enrollment_id: str) -> np.ndarray:
    """Return this enrollment's sequences, oldest first.

    Shape: (n_sequences, seq_len, 21). Chronological order is load-bearing —
    the promotion gate holds out the *newest* sequences.
    """
    rows = conn.execute(
        'SELECT data_json FROM fused_sequences WHERE enrollment_id = ? ORDER BY seq_end_ns',
        [enrollment_id],
    ).fetchall()
    if not rows:
        return np.empty((0, 0, 0), dtype=np.float32)
    return np.array([json.loads(r[0]) for r in rows], dtype=np.float32)


def count_sequences(conn, enrollment_id: str) -> int:
    return conn.execute(
        'SELECT count(*) FROM fused_sequences WHERE enrollment_id = ?',
        [enrollment_id],
    ).fetchone()[0]


def active_minutes(conn, enrollment_id: str, stride_sec: int) -> float:
    """Approximate minutes of *active* use captured for this enrollment.

    Each retained window contributes one stride of new activity; idle windows
    were never stored, so this is activity time rather than wall-clock time.
    """
    n = conn.execute(
        'SELECT count(*) FROM feature_windows WHERE enrollment_id = ?',
        [enrollment_id],
    ).fetchone()[0]
    return (n or 0) * stride_sec / 60.0


def distinct_hours(conn, enrollment_id: str) -> int:
    """How many distinct hours-of-day this enrollment has seen activity in."""
    return conn.execute(
        'SELECT count(DISTINCT hour(to_timestamp(window_start_ns / 1e9))) '
        'FROM feature_windows WHERE enrollment_id = ?',
        [enrollment_id],
    ).fetchone()[0] or 0
