"""Incremental feature extraction: the daemon calls this every tick, so it has
to be safe to call repeatedly and it must refuse to build sequences that span
an idle gap."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


from behavioral_auth.features.pipeline import build_feature_windows, build_sequences

SEC = 1_000_000_000


def _session(conn, cfg):
    sid, eid = str(uuid.uuid4()), str(uuid.uuid4())
    conn.execute(
        'INSERT INTO enrollments (enrollment_id, status) VALUES (?, ?)', [eid, 'learning'])
    conn.execute(
        'INSERT INTO sessions (session_id, user_name, mode) VALUES (?, ?, ?)',
        [sid, 'test', 'test'])
    return sid, eid


def _type(conn, sid, t0_ns: int, duration_sec: int, rate_hz: int = 8) -> None:
    """Insert plausible keyboard events over [t0, t0+duration)."""
    rows = []
    step = SEC // rate_hz
    for i in range(duration_sec * rate_hz):
        ts = t0_ns + i * step
        code = 30 + (i % 20)
        for value, offset in ((1, 0), (0, step // 3)):
            rows.append((ts + offset,
                         datetime.fromtimestamp((ts + offset) / 1e9, tz=timezone.utc),
                         sid, '/dev/x', 'kbd', 'keyboard', 1, code, value))
    conn.executemany(
        'INSERT INTO raw_events (ts_ns, ts_utc, session_id, dev_path, dev_name, '
        'dev_type, ev_type, ev_code, ev_value) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', rows)


def test_windows_are_idempotent(conn, cfg):
    """Calling the pipeline again must not re-insert windows it already built.

    The old code rebuilt every window from the session start on each run, with
    no unique key — a daemon ticking every 5s would have duplicated the entire
    session over and over.
    """
    sid, eid = _session(conn, cfg)
    t0 = 1_700_000_000 * SEC
    _type(conn, sid, t0, duration_sec=60)

    first = build_feature_windows(conn, sid, eid, cfg)
    assert first > 0

    second = build_feature_windows(conn, sid, eid, cfg)
    assert second == 0, 'a second pass invented new windows'

    total = conn.execute('SELECT count(*) FROM feature_windows').fetchone()[0]
    unique = conn.execute(
        'SELECT count(*) FROM (SELECT DISTINCT session_id, window_start_ns '
        'FROM feature_windows)').fetchone()[0]
    assert total == unique == first


def test_windows_resume_from_watermark(conn, cfg):
    """New events produce only the newly-closed windows."""
    sid, eid = _session(conn, cfg)
    t0 = 1_700_000_000 * SEC
    _type(conn, sid, t0, duration_sec=40)
    first = build_feature_windows(conn, sid, eid, cfg)

    _type(conn, sid, t0 + 40 * SEC, duration_sec=20)
    second = build_feature_windows(conn, sid, eid, cfg)

    assert second > 0
    assert second < first          # only the tail, not the whole session again


def test_sequences_reject_idle_gaps(conn, cfg):
    """A sequence must not splice behaviour from either side of a long break.

    Windows below the activity threshold are dropped, so feature_windows rows
    are not time-contiguous. Without the gap guard a single training sample
    could contain Monday morning and Tuesday evening.
    """
    sid, eid = _session(conn, cfg)
    t0 = 1_700_000_000 * SEC
    _type(conn, sid, t0, duration_sec=30)
    _type(conn, sid, t0 + 3600 * SEC, duration_sec=30)   # an hour later

    build_feature_windows(conn, sid, eid, cfg)
    build_sequences(conn, sid, eid, cfg)

    rows = conn.execute(
        'SELECT seq_end_ns FROM fused_sequences WHERE session_id = ?', [sid]).fetchall()
    assert rows, 'no sequences at all — the guard rejected everything'

    gap_ns = cfg.features.max_seq_gap_sec * SEC
    # No stored sequence may straddle the hour-long hole.
    boundary = t0 + 3600 * SEC
    for (seq_end,) in rows:
        span_start = seq_end - (cfg.model.seq_len * cfg.features.stride_sec
                                + cfg.features.window_sec) * SEC
        assert not (span_start < boundary - gap_ns and seq_end > boundary), \
            'a sequence spans the idle gap'


def test_sequences_are_idempotent(conn, cfg):
    sid, eid = _session(conn, cfg)
    _type(conn, sid, 1_700_000_000 * SEC, duration_sec=60)
    build_feature_windows(conn, sid, eid, cfg)

    first = build_sequences(conn, sid, eid, cfg)
    assert first > 0
    assert build_sequences(conn, sid, eid, cfg) == 0


def _mouse_clicks(conn, sid, t0_ns: int, n: int, step_ns: int = 200_000_000) -> None:
    """Mouse button events only -- plenty of rows, no cursor movement at all."""
    rows = []
    for i in range(n):
        ts = t0_ns + i * step_ns
        rows.append((ts, datetime.fromtimestamp(ts / 1e9, tz=timezone.utc),
                     sid, '/dev/m', 'mouse', 'mouse', 1, 272, i % 2))
    conn.executemany(
        'INSERT INTO raw_events (ts_ns, ts_utc, session_id, dev_path, dev_name, '
        'dev_type, ev_type, ev_code, ev_value) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', rows)


def test_unextractable_windows_are_not_stored_as_zeros(conn, cfg):
    """Passing the event-count gate is not the same as having features.

    These windows hold far more mouse rows than min_mouse_events, so the
    retention check waves them through -- but there is no cursor motion, so the
    mouse extractor returns None, and there are no keystrokes either. The old
    code stored the row anyway with all 17 behavioural features at 0.0.

    Those rows are indistinguishable from "the user sat perfectly still". They
    were counted as active minutes, trained on, and used to build the synthetic
    impostors that guard promotion -- and because scaling a zero changes
    nothing, they made the promotion gate impossible to satisfy.
    """
    sid, eid = _session(conn, cfg)
    t0 = 1_700_000_000 * SEC
    _mouse_clicks(conn, sid, t0, n=300)      # 60s of clicking, no movement

    inserted = build_feature_windows(conn, sid, eid, cfg)
    assert inserted == 0, 'stored a window whose extractors both returned None'

    stored = conn.execute('SELECT count(*) FROM feature_windows').fetchone()[0]
    assert stored == 0


def test_real_activity_is_still_stored(conn, cfg):
    """The guard above must not throw away windows that do have features."""
    sid, eid = _session(conn, cfg)
    t0 = 1_700_000_000 * SEC
    _type(conn, sid, t0, duration_sec=60)

    assert build_feature_windows(conn, sid, eid, cfg) > 0
    nonzero = conn.execute(
        'SELECT count(*) FROM feature_windows WHERE f_ks_count > 0').fetchone()[0]
    assert nonzero > 0
