"""The pure event-shaping the Windows hook depends on (no pynput, no Windows)."""

import inspect
import json

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
from behavioral_auth.collector import windows_source
from behavioral_auth.collector.windows_source import (
    INJECTION_MIN_SAMPLE,
    INJECTION_WARN_SHARE,
    InjectionStats,
    _Shaper,
)


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


# ── injected-input accounting ────────────────────────────────────────────────
#
# Windows answers half of "was this input human?" for free: both low-level hooks
# set a flag on events synthesised by SendInput, and pynput hands the whole hook
# structure to an event_filter. The flag was already reaching Python and was
# being dropped. Pure counting, so these run on any OS.

def test_a_clean_stream_reports_nothing_injected():
    s = InjectionStats()
    for _ in range(10):
        s.record_keyboard(0)
        s.record_mouse(0)
    assert s.keyboard_share == 0.0
    assert s.mouse_share == 0.0
    assert s.loud_channels() == []


def test_the_two_structures_put_the_injected_bit_in_different_places():
    """MSLLHOOKSTRUCT uses 0x01, KBDLLHOOKSTRUCT uses 0x10. One shared mask would
    both miss real injection and invent it — 0x10 on a mouse event is a genuine
    unrelated flag, and 0x01 on a keyboard event is LLKHF_EXTENDED."""
    s = InjectionStats()
    s.record_mouse(0x01)                 # LLMHF_INJECTED
    s.record_keyboard(0x10)              # LLKHF_INJECTED
    assert s.mouse_injected == 1
    assert s.keyboard_injected == 1

    other = InjectionStats()
    other.record_mouse(0x10)             # not the mouse injected bit
    other.record_keyboard(0x01)          # LLKHF_EXTENDED, not injection
    assert other.mouse_injected == 0
    assert other.keyboard_injected == 0


def test_lower_integrity_injection_counts_too():
    """Input synthesised by a *less* privileged process is more interesting, not
    less — it is what a sandboxed or unprivileged tool driving the desktop looks
    like."""
    s = InjectionStats()
    s.record_mouse(0x02)                 # LLMHF_LOWER_IL_INJECTED
    s.record_keyboard(0x02)              # LLKHF_LOWER_IL_INJECTED
    assert s.mouse_injected == 1
    assert s.keyboard_injected == 1


def test_shares_are_per_channel():
    s = InjectionStats()
    for _ in range(3):
        s.record_mouse(0x01)
    s.record_mouse(0)
    for _ in range(4):
        s.record_keyboard(0)
    assert s.mouse_share == 0.75
    assert s.keyboard_share == 0.0


def test_an_empty_channel_is_zero_not_a_division_by_zero():
    s = InjectionStats()
    assert s.keyboard_share == 0.0
    assert s.mouse_share == 0.0
    assert s.as_dict()['mouse_total'] == 0


def test_a_small_sample_never_shouts():
    """Without the floor the very first injected event reads as 100 % and the
    daemon would warn on its first tick, every time."""
    s = InjectionStats()
    s.record_mouse(0x01)
    assert s.mouse_share == 1.0
    assert s.loud_channels() == []


def test_a_large_injected_share_is_loud_on_exactly_the_channel_that_is_dirty():
    s = InjectionStats()
    for _ in range(INJECTION_MIN_SAMPLE):
        s.record_mouse(0x01)             # a jiggler: every mouse event synthetic
        s.record_keyboard(0)             # ...and the keyboard untouched
    assert s.loud_channels() == ['mouse']


def test_the_threshold_is_a_floor_not_a_strict_inequality():
    s = InjectionStats()
    injected = int(INJECTION_MIN_SAMPLE * INJECTION_WARN_SHARE)
    for i in range(INJECTION_MIN_SAMPLE):
        s.record_mouse(0x01 if i < injected else 0)
    assert s.mouse_share == INJECTION_WARN_SHARE
    assert s.loud_channels() == ['mouse']


def test_as_dict_is_json_safe_and_closed():
    s = InjectionStats()
    s.record_mouse(0x01)
    payload = s.as_dict()
    assert set(payload) == {
        'keyboard_total', 'keyboard_injected', 'mouse_total', 'mouse_injected',
        'keyboard_share', 'mouse_share'}
    json.dumps(payload)                  # it rides in state.json


def test_the_filters_must_never_suppress_the_users_own_input():
    """pynput treats a filter returning exactly False as "swallow this event".
    These filters exist to count, and a counting filter that ate keystrokes would
    be a keyboard that stops working.
    """
    src = inspect.getsource(windows_source.run_windows_hook)
    body = src[src.index('def _kbd_filter'):src.index('kbd = keyboard.Listener')]
    # Non-vacuous: an empty slice would satisfy the assertion below for free.
    assert 'record_keyboard' in body and 'record_mouse' in body
    assert 'return' not in body, 'the filters must fall off the end, returning None'


def test_the_filters_are_wired_under_the_platform_prefixed_option_name():
    """`win32_event_filter`, not `event_filter`.

    pynput builds its backend options from keywords carrying a platform prefix
    and **silently drops** any that do not (mouse/_base.py:253) — no error, and a
    listener that still reports `running`. Measured on Windows: with the
    unprefixed name the filter was called zero times while input kept flowing, so
    the counters would have sat at 0.0 % forever and reported a clean machine.
    Nothing but this assertion would have caught it.
    """
    src = inspect.getsource(windows_source.run_windows_hook)
    assert 'win32_event_filter=_kbd_filter' in src
    assert 'win32_event_filter=_mouse_filter' in src
