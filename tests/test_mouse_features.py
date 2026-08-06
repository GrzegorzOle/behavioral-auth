"""Mouse dynamics extraction.

This path had no coverage at all, which is how three defects rode into a
release together: the two axes were zipped by position although they are
separate streams of unequal length, their values (which are deltas) were
differenced a second time, and a window whose extraction failed was still
stored as a row of zeros.

The tests below assert the *shape of the data*, not just that a dict comes
back. A test that only checked "9 keys returned" passes on every one of those
bugs.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from behavioral_auth.features.mouse import extract_mouse_features

MS = 1_000_000


def _rows(events) -> pd.DataFrame:
    """events: iterable of (ts_ns, ev_type, ev_code, ev_value)."""
    return pd.DataFrame(
        [{'dev_type': 'mouse', 'ts_ns': t, 'ev_type': ty, 'ev_code': c, 'ev_value': v}
         for t, ty, c, v in events],
        columns=['dev_type', 'ts_ns', 'ev_type', 'ev_code', 'ev_value'])


def _steady_move(n: int, dx: int, dy: int, step_ns: int = 8 * MS, t0: int = 0):
    """n movement samples, each reporting both axes at one timestamp."""
    out = []
    for i in range(n):
        ts = t0 + i * step_ns
        if dx:
            out.append((ts, 2, 0, dx))
        if dy:
            out.append((ts, 2, 1, dy))
    return out


def test_speed_is_distance_per_second_not_a_second_derivative():
    """Values are deltas, so distance per sample is hypot(dx, dy) itself.

    Differencing them again — which is what the code used to do — makes a
    perfectly steady drag report a speed of zero, because the delta never
    changes. This is the assertion that catches it.
    """
    # 30 px right + 40 px down every 10 ms => 50 px per 10 ms => 5000 px/s.
    f = extract_mouse_features(_rows(_steady_move(10, 30, 40, step_ns=10 * MS)))
    assert f is not None
    assert f['f_ms_speed_mean'] == 5000.0
    assert f['f_ms_speed_std'] == 0.0
    # Steady speed means no acceleration. Under the old double-difference this
    # was not merely wrong in scale, it was wrong in kind.
    assert f['f_ms_acc_mean'] == 0.0


def test_single_axis_motion_still_yields_features():
    """A purely horizontal drag reports REL_X only — REL_Y is not 'missing'.

    The old code took n = min(len(x), len(y)), so len(y) == 0 collapsed the
    whole extractor to None and the caller stored nine zeros instead. An
    anti-idle tool that nudges the cursor sideways produced exactly this.
    """
    f = extract_mouse_features(_rows(_steady_move(10, 12, 0, step_ns=10 * MS)))
    assert f is not None
    assert f['f_ms_count'] == 10
    assert f['f_ms_speed_mean'] == 1200.0     # 12 px per 10 ms
    assert f['f_ms_curvature'] == 0.0         # straight line, no direction change


def test_axes_are_paired_by_time_not_by_position():
    """Unequal stream lengths must not shift one axis against the other.

    Here every sample moves +10 in x, but only every other sample moves in y.
    Zipping by position would pair x[i] with the y of a different moment and
    invent direction changes that never happened.
    """
    events = []
    for i in range(8):
        ts = i * 10 * MS
        events.append((ts, 2, 0, 10))
        if i % 2 == 0:
            events.append((ts, 2, 1, 10))
    f = extract_mouse_features(_rows(events))
    assert f is not None
    assert f['f_ms_count'] == 8
    # Samples are (10,10) on even i and (10,0) on odd. Speed is defined from the
    # second sample onward, so seven of them: four flat, three diagonal. Spelling
    # the series out is the point — a positional zip pairs x with the y of another
    # moment and cannot reproduce these exact values.
    speeds = [np.hypot(10, 10 if i % 2 == 0 else 0) / 0.01 for i in range(1, 8)]
    assert f['f_ms_speed_mean'] == float(np.mean(speeds))
    assert f['f_ms_speed_std'] == float(np.std(speeds))


def test_scroll_events_are_not_read_as_movement():
    """REL_WHEEL is ev_type 2 as well, but it is not a cursor movement.

    It used to enter the movement series, contributing a timestamp with no
    axis and stretching the speed series against the wrong clock.
    """
    events = _steady_move(6, 20, 0, step_ns=10 * MS)
    # A burst of scrolling in the middle, at timestamps of its own.
    events += [(3 * 10 * MS + 2 * MS, 2, 8, 1) for _ in range(5)]
    f = extract_mouse_features(_rows(sorted(events)))
    assert f is not None
    assert f['f_ms_count'] == 6            # scrolls are not movement samples
    assert f['f_ms_scrolls'] == 5
    assert f['f_ms_speed_mean'] == 2000.0  # unchanged by the scrolling


def test_too_little_movement_returns_none_rather_than_zeros():
    """Fewer than three samples cannot give a speed series.

    Returning None is what lets the caller drop the window; the bug was never
    this branch but the caller treating None as 'all features are zero'.
    """
    assert extract_mouse_features(_rows(_steady_move(2, 5, 5))) is None
    assert extract_mouse_features(_rows([])) is None
    # Clicks alone, no motion at all.
    assert extract_mouse_features(_rows([(0, 1, 272, 1), (5 * MS, 1, 272, 0)])) is None


def test_clicks_and_dwell_survive_alongside_motion():
    events = _steady_move(6, 10, 10, step_ns=10 * MS)
    events += [(0, 1, 272, 1), (20 * MS, 1, 272, 0)]     # 20 ms hold
    f = extract_mouse_features(_rows(sorted(events)))
    assert f is not None
    assert f['f_ms_clicks'] == 1
    assert f['f_ms_click_dwell'] == 20.0


# ── two reports inside one clock tick ────────────────────────────────────────
#
# The defect this closes reached production. `c in cur` started a new sample
# whenever an axis repeated, even when the timestamp had not moved, so two
# REL_X in one tick became two samples separated by zero. dt was then floored at
# 1e-6 s -- a millionfold amplifier -- and speed = distance / dt did the rest:
# 4.3e6 px/s against a median of 1 208, accelerations to 4.4e12.

def _rel(rows):
    """rows: (ts_ns, ev_code, ev_value) -> the frame extract_mouse_features wants."""
    import pandas as pd
    return pd.DataFrame([
        {'ts_ns': t, 'dev_type': 'mouse', 'ev_type': 2, 'ev_code': c, 'ev_value': v}
        for t, c, v in rows])


def test_an_axis_repeating_in_the_same_tick_does_not_split_the_sample():
    from behavioral_auth.features.mouse import _motion_samples
    ts, dx, dy = _motion_samples(_rel([
        (1_000_000_000, 0, 3),      # X +3
        (1_000_000_000, 0, 4),      # X +4, same instant -> same sample
        (1_008_000_000, 0, 5),      # 8 ms later -> a new sample
    ]))
    assert list(ts) == [1_000_000_000, 1_008_000_000]
    assert list(dx) == [7.0, 5.0], 'the two reports in one tick must sum'


def test_two_axes_in_one_tick_still_make_one_sample():
    """The behaviour that was already right must survive the fix."""
    from behavioral_auth.features.mouse import _motion_samples
    ts, dx, dy = _motion_samples(_rel([
        (1_000_000_000, 0, 3),
        (1_000_000_000, 1, -2),
    ]))
    assert len(ts) == 1 and dx[0] == 3.0 and dy[0] == -2.0


def test_a_repeat_after_the_tick_advances_still_splits():
    from behavioral_auth.features.mouse import _motion_samples
    ts, _, _ = _motion_samples(_rel([
        (1_000_000_000, 0, 3),
        (1_000_500_000, 0, 4),      # 0.5 ms later: a real second movement
    ]))
    assert len(ts) == 2


def test_speed_stays_physical_when_a_tick_carries_several_reports():
    """The end-to-end assertion. Before the fix this window reported speeds in
    the millions of pixels per second from a hand that moved seven pixels."""
    from behavioral_auth.features.mouse import extract_mouse_features
    rows = []
    for i in range(20):
        t = 1_000_000_000 + i * 8_000_000        # a healthy 125 Hz mouse
        rows += [(t, 0, 3), (t, 0, 4), (t, 1, 2)]
    f = extract_mouse_features(_rel(rows))
    assert f is not None
    assert f['f_ms_speed_mean'] < 10_000, f"speed exploded: {f['f_ms_speed_mean']}"
    assert abs(f['f_ms_acc_mean']) < 1e7, f"acceleration exploded: {f['f_ms_acc_mean']}"


def test_the_floor_is_a_millisecond_not_a_microsecond():
    """A sub-millisecond gap is the clock, not the mouse. The old 1e-6 floor
    bounded the damage at a million times instead of preventing it."""
    from behavioral_auth.features import mouse
    assert mouse._MIN_DT_SEC == 1e-3
