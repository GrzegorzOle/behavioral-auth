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
