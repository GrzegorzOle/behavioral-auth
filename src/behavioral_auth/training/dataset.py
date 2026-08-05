"""Training dataset loader.

Scoped to a single enrollment: a pattern must never be trained on sequences
belonging to a retired enrollment, or to a test/impostor session.

**And never to input that arrived over a remote display protocol.** RDP is not
different hardware, it is a different transport: the same person, with the link's
latency and input batching written into the keystroke timings the model reads.
Training on it does not merely add noise, it teaches the pattern a distortion
that varies minute to minute with the network. Every query below that feeds
training or a promotion gate therefore excludes remote stacks — including the
gate counters, because a gate satisfied by data the model is not allowed to see
would be satisfied on false evidence.

Collection keeps those rows: they cost nothing, they are honestly tagged, and a
future decision (a per-transport pattern, say) would want them.
"""

from __future__ import annotations

import json

import numpy as np

from behavioral_auth.collector.stack import REMOTE_IDS, consolidate

# Matched against stack_fp, which is 'keyboard/mouse'. A row is remote when
# either half is, so a substring test over the whole key is exactly right and
# needs no parsing in SQL.
_REMOTE_PREDICATE = ' AND ' + ' AND '.join(
    f"coalesce(stack_fp, '') NOT LIKE '%{rid}%'" for rid in sorted(REMOTE_IDS))


def load_sequences(conn, enrollment_id: str) -> np.ndarray:
    """Return this enrollment's sequences, oldest first.

    Shape: (n_sequences, seq_len, 21). Chronological order is load-bearing —
    the promotion gate holds out the *newest* sequences.
    """
    rows = conn.execute(
        'SELECT data_json FROM fused_sequences WHERE enrollment_id = ?'
        + _REMOTE_PREDICATE + ' ORDER BY seq_end_ns',
        [enrollment_id],
    ).fetchall()
    if not rows:
        return np.empty((0, 0, 0), dtype=np.float32)
    return np.array([json.loads(r[0]) for r in rows], dtype=np.float32)


def trained_stacks(conn, enrollment_id: str) -> list[str]:
    """Every hardware stack the enrolment's sequences came from.

    This is what a promoted pattern is allowed to be scored against. More than
    one entry means the pattern is a mixture, and a mixture is more permissive
    than a pattern learned on a single stack — the caller says so out loud.
    """
    rows = conn.execute(
        'SELECT DISTINCT stack_fp FROM fused_sequences '
        'WHERE enrollment_id = ? AND stack_fp IS NOT NULL'
        + _REMOTE_PREDICATE + ' ORDER BY stack_fp',
        [enrollment_id],
    ).fetchall()
    # Consolidated: a keyboard-only key and a keyboard+mouse key from the same
    # setup are one stack, not two. Counting the raw set would call every
    # ordinary enrolment a mixture.
    return consolidate(r[0] for r in rows)


def count_sequences(conn, enrollment_id: str) -> int:
    return conn.execute(
        'SELECT count(*) FROM fused_sequences WHERE enrollment_id = ?' + _REMOTE_PREDICATE,
        [enrollment_id],
    ).fetchone()[0]


def active_minutes(conn, enrollment_id: str, stride_sec: int) -> float:
    """Approximate minutes of *active* use captured for this enrollment.

    Each retained window contributes one stride of new activity, so this is
    activity time rather than wall-clock time. That rests entirely on
    build_feature_windows refusing to store a window whose extractors both
    returned None: while it did store those, this number counted nights and
    weekends nobody was present, and the volume gate passed on that evidence.
    """
    n = conn.execute(
        'SELECT count(*) FROM feature_windows WHERE enrollment_id = ?' + _REMOTE_PREDICATE,
        [enrollment_id],
    ).fetchone()[0]
    return (n or 0) * stride_sec / 60.0


def distinct_hours(conn, enrollment_id: str) -> int:
    """How many distinct hours-of-day this enrollment has seen activity in."""
    return conn.execute(
        'SELECT count(DISTINCT hour(to_timestamp(window_start_ns / 1e9))) '
        'FROM feature_windows WHERE enrollment_id = ?' + _REMOTE_PREDICATE,
        [enrollment_id],
    ).fetchone()[0] or 0
