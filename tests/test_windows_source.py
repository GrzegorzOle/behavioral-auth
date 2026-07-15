"""The pure event-shaping the Windows hook depends on (no pynput, no Windows)."""

from behavioral_auth.collector.keycodes import (
    BTN_LEFT,
    BTN_MIDDLE,
    BTN_RIGHT,
    EV_KEY,
    EV_REL,
    REL_WHEEL,
    REL_X,
    REL_Y,
)
from behavioral_auth.collector.windows_source import _Shaper


def test_first_press_is_1_held_is_autorepeat_2_release_is_0():
    s = _Shaper()
    assert s.key(0x41, pressed=True) == (EV_KEY, 30, 1)     # 'A' first down
    assert s.key(0x41, pressed=True) == (EV_KEY, 30, 2)     # still held -> repeat
    assert s.key(0x41, pressed=True) == (EV_KEY, 30, 2)     # still repeat
    assert s.key(0x41, pressed=False) == (EV_KEY, 30, 0)    # up
    assert s.key(0x41, pressed=True) == (EV_KEY, 30, 1)     # fresh press again


def test_held_state_is_per_key():
    s = _Shaper()
    assert s.key(0x41, pressed=True)[2] == 1
    assert s.key(0x42, pressed=True)[2] == 1                # different key, still 1
    assert s.key(0x41, pressed=True)[2] == 2                # 'A' still held


def test_backspace_shapes_to_code_14():
    assert _Shaper().key(0x08, pressed=True) == (EV_KEY, 14, 1)


def test_first_move_sets_origin_then_reports_deltas():
    s = _Shaper()
    assert s.move(100, 100) == []                           # origin only
    assert s.move(105, 100) == [(EV_REL, REL_X, 5)]         # x moved, y didn't
    assert s.move(105, 97) == [(EV_REL, REL_Y, -3)]         # y moved, x didn't
    assert s.move(110, 90) == [(EV_REL, REL_X, 5), (EV_REL, REL_Y, -7)]


def test_clicks_map_to_evdev_buttons():
    s = _Shaper()
    assert s.click('left', pressed=True) == (EV_KEY, BTN_LEFT, 1)
    assert s.click('left', pressed=False) == (EV_KEY, BTN_LEFT, 0)
    assert s.click('right', pressed=True) == (EV_KEY, BTN_RIGHT, 1)
    assert s.click('middle', pressed=True) == (EV_KEY, BTN_MIDDLE, 1)


def test_scroll_sign_only():
    s = _Shaper()
    assert s.scroll(1.0) == (EV_REL, REL_WHEEL, 1)
    assert s.scroll(-3.0) == (EV_REL, REL_WHEEL, -1)
    assert s.scroll(0) is None
