"""Keyboard pseudonymisation: zones and pairing ids instead of key codes.

Pure logic, so these run on any OS. The one test that matters most is
test_no_key_code_survives_the_writer -- everything else here is detail around
the single invariant the change exists to establish.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from behavioral_auth.collector.zones import (
    ZONE_BACKSPACE, ZONE_LEFT_HOME, ZONE_LEFT_TOP, ZONE_MODIFIER, ZONE_OTHER,
    ZONE_RIGHT_HOME, ZONE_SPACE, KeyPseudonymiser, zone_of,
)
from behavioral_auth.features.keystroke import extract_keystroke_features

SEC = 1_000_000_000


def _row(ts, ev_type, code, value, dev_type='keyboard'):
    return (ts, None, 'sid', '/dev/input/event0', 'kbd', 'v:p', dev_type,
            ev_type, code, value)


def test_zone_of_maps_hands_and_rows():
    assert zone_of(30) == ZONE_LEFT_HOME       # a
    assert zone_of(34) == ZONE_LEFT_HOME       # g
    assert zone_of(35) == ZONE_RIGHT_HOME      # h
    assert zone_of(16) == ZONE_LEFT_TOP        # q
    assert zone_of(57) == ZONE_SPACE
    assert zone_of(14) == ZONE_BACKSPACE
    assert zone_of(42) == ZONE_MODIFIER        # left shift


def test_backspace_keeps_a_zone_of_its_own():
    """f_ks_backspace_ratio counts backspace and nothing else.

    Folding Delete into a shared 'edit' zone would quietly redefine a feature
    that a live pattern is already built on, so Delete is OTHER, not backspace.
    """
    assert zone_of(14) == ZONE_BACKSPACE
    assert zone_of(111) != ZONE_BACKSPACE      # delete


def test_unmapped_and_windows_fallback_codes_are_other():
    """collector/keycodes.py sends anything it cannot map to 1000+vk."""
    assert zone_of(1000 + 0x5B) == ZONE_OTHER
    assert zone_of(63) == ZONE_OTHER           # F5


def test_no_key_code_survives_the_writer():
    """The invariant the whole change exists for.

    Every source funnels through Writer.add, so this is the property that makes
    "no keylog on disk" true of the product rather than of three files.
    """
    p = KeyPseudonymiser()
    for code in (30, 14, 57, 16, 42, 1000 + 0x5B):
        down = p.transform(_row(0, 1, code, 1))
        up = p.transform(_row(1, 1, code, 0))
        assert down[8] == 0 and up[8] == 0, f'ev_code survived for {code}'
        assert down[10] == zone_of(code)


def test_mouse_rows_keep_their_ev_code():
    """Mouse ev_code is the axis or button the mouse features read.

    Zeroing it would destroy REL_X/REL_Y/wheel and every click, so the
    transform has to key on dev_type rather than on ev_type alone -- a mouse
    button is an EV_KEY event just as a keystroke is.
    """
    p = KeyPseudonymiser()
    for code in (0, 1, 8):                     # REL_X, REL_Y, REL_WHEEL
        moved = p.transform(_row(0, 2, code, 5, dev_type='mouse'))
        assert moved[8] == code
        assert moved[10] is None and moved[11] is None
    for code in (272, 273, 274):               # left, right, middle button
        clicked = p.transform(_row(1, 1, code, 1, dev_type='mouse'))
        assert clicked[8] == code
        assert clicked[10] is None and clicked[11] is None


def test_rollover_pairs_correctly_where_a_zone_alone_would_not():
    """a-down, s-down, a-up, s-up -- both letters are in the same zone.

    Keying the dwell map on the zone would mispair a-up with s-down. This is
    normal fluent typing, not an edge case, which is why a pairing id exists.
    """
    p = KeyPseudonymiser()
    a_down = p.transform(_row(0, 1, 30, 1))    # 'a', left-home
    s_down = p.transform(_row(1, 1, 31, 1))    # 's', left-home
    a_up = p.transform(_row(2, 1, 30, 0))
    s_up = p.transform(_row(3, 1, 31, 0))
    assert a_down[10] == s_down[10] == ZONE_LEFT_HOME
    assert a_down[11] != s_down[11], 'held keys must get distinct pair ids'
    assert a_up[11] == a_down[11]
    assert s_up[11] == s_down[11]


def test_pair_ids_are_reused_after_release():
    """Uniqueness is only required among keys currently held."""
    p = KeyPseudonymiser()
    first = p.transform(_row(0, 1, 30, 1))[11]
    p.transform(_row(1, 1, 30, 0))
    second = p.transform(_row(2, 1, 31, 1))[11]
    assert second == first


def test_auto_repeat_keeps_the_pair_of_the_held_key():
    p = KeyPseudonymiser()
    down = p.transform(_row(0, 1, 30, 1))
    rep = p.transform(_row(1, 1, 30, 2))
    up = p.transform(_row(2, 1, 30, 0))
    assert rep[11] == down[11] == up[11]


def test_release_without_a_press_does_not_crash():
    """A window can start mid-hold, and a daemon can restart mid-hold."""
    p = KeyPseudonymiser()
    up = p.transform(_row(0, 1, 30, 0))
    assert up[8] == 0 and up[10] == ZONE_LEFT_HOME


def test_held_map_is_bounded():
    """A stuck or synthetic stream must not grow the map without bound."""
    p = KeyPseudonymiser()
    for code in range(2, 500):
        p.transform(_row(code, 1, code, 1))
    assert len(p._held) <= 64


def _frame(rows):
    return pd.DataFrame(rows, columns=['ts_ns', 'ev_type', 'ev_code',
                                       'ev_value', 'kb_zone', 'kb_pair'])


def test_features_match_between_pseudonymised_and_legacy_rows():
    """The point of the exercise: identical features, no key codes stored.

    The same keystrokes are extracted twice -- once as pre-005 rows carrying
    real codes, once as pseudonymised rows -- and every feature must agree. If
    they did not, the change would be silently shifting a live pattern.
    """
    p = KeyPseudonymiser()
    presses = [(30, 40), (31, 55), (14, 30), (57, 60), (35, 45)]
    legacy, pseudo, t = [], [], 0
    for code, hold_ms in presses:
        for value in (1, 0):
            ts = t if value == 1 else t + hold_ms * 1_000_000
            legacy.append((ts, 1, code, value, np.nan, np.nan))
            out = p.transform(_row(ts, 1, code, value))
            pseudo.append((ts, 1, out[8], value, out[10], out[11]))
        t += (hold_ms + 120) * 1_000_000

    a = extract_keystroke_features(_frame(legacy))
    b = extract_keystroke_features(_frame(pseudo))
    assert a is not None and b is not None
    for name in a:
        assert a[name] == pytest.approx(b[name]), name
    assert b['f_ks_backspace_ratio'] > 0, 'backspace must survive as a zone'
    assert all(r[2] == 0 for r in pseudo), 'no key code in the stored rows'


def test_window_spanning_the_upgrade_reads_both_shapes():
    """A window straddling the migration holds legacy and pseudonymised rows."""
    p = KeyPseudonymiser()
    rows = []
    for ts, code in ((0, 30), (200_000_000, 31)):        # legacy
        rows.append((ts, 1, code, 1, np.nan, np.nan))
        rows.append((ts + 40_000_000, 1, code, 0, np.nan, np.nan))
    for ts, code in ((400_000_000, 32), (600_000_000, 33)):
        for value in (1, 0):
            at = ts if value == 1 else ts + 40_000_000
            out = p.transform(_row(at, 1, code, value))
            rows.append((at, 1, out[8], value, out[10], out[11]))
    f = extract_keystroke_features(_frame(rows))
    assert f is not None
    assert f['f_ks_count'] == 8
    assert f['f_ks_mean_dwell'] == pytest.approx(40.0)
