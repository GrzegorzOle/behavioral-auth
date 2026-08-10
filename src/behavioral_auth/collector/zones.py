"""Keyboard zones: what gets stored instead of which key was pressed.

The daemon used to persist the evdev key code of every keystroke, which is a
keylog. It never needed one. `features/keystroke.py` uses the code for exactly
two things:

  * pairing a key-down with its key-up, so dwell can be measured — that needs
    only *equality*, never identity;
  * spotting backspace, for the correction ratio — one bit.

Nothing computes anything per key. `f_ks_entropy`, despite the name, is the
entropy of the *dwell-time* histogram, not of the key distribution. So the code
itself was gratuitous, and storing it was a privacy cost paid for nothing.

What is stored instead:

  * **zone** — hand x row, plus space, backspace, modifiers and everything else.
    Today's eight features do not read it at all; it is kept because hand
    alternation and same-row transitions are the standard strong signals in
    keystroke dynamics, and 2026-08-10 measured that the keyboard channel is
    where identity has to come from. Dropping to a bare "ordinary key" flag
    would have closed that door.
  * **pair id** — a small integer handed out at key-down and echoed at key-up,
    unique only among the keys *currently held*. That is all pairing needs, and
    it survives rollover, which a zone alone does not: typing "as" quickly gives
    a-down, s-down, a-up, s-up, and if both letters share a zone then a
    zone-keyed map mispairs a-up with s-down and reports a nonsense dwell.
    Rollover is normal in fluent typing, not an edge case.

**Hashing the key code was rejected, deliberately.** The domain is about 256
values, so a rainbow table over it is instant, and the salt would have to be
stored for pairing to survive a restart. It would have looked like protection
and provided none.

**What a zone stream still leaks, said plainly:** zones plus timings are a very
lossy transcript, not a blank one. With enough text they narrow the space of
what was typed. They do not reveal it. This is a large reduction, not an
absolute guarantee, and the README should not claim otherwise.

Pure integer arithmetic and a small dict — no evdev, no Windows API — so it
imports and is unit-tested on any platform. Windows codes arrive already
translated into evdev's space by `collector/keycodes.py`; anything it could not
map lands at 1000+vk, which falls through to OTHER.
"""

from __future__ import annotations

ZONE_UNKNOWN = 0
ZONE_LEFT_NUM = 1
ZONE_LEFT_TOP = 2
ZONE_LEFT_HOME = 3
ZONE_LEFT_BOTTOM = 4
ZONE_RIGHT_NUM = 5
ZONE_RIGHT_TOP = 6
ZONE_RIGHT_HOME = 7
ZONE_RIGHT_BOTTOM = 8
ZONE_SPACE = 9
ZONE_BACKSPACE = 10
ZONE_MODIFIER = 11
ZONE_OTHER = 12

ZONE_NAMES = {
    ZONE_UNKNOWN: 'unknown', ZONE_LEFT_NUM: 'left-number',
    ZONE_LEFT_TOP: 'left-top', ZONE_LEFT_HOME: 'left-home',
    ZONE_LEFT_BOTTOM: 'left-bottom', ZONE_RIGHT_NUM: 'right-number',
    ZONE_RIGHT_TOP: 'right-top', ZONE_RIGHT_HOME: 'right-home',
    ZONE_RIGHT_BOTTOM: 'right-bottom', ZONE_SPACE: 'space',
    ZONE_BACKSPACE: 'backspace', ZONE_MODIFIER: 'modifier',
    ZONE_OTHER: 'other',
}

# Backspace keeps a zone of its own rather than joining a general "edit" class.
# f_ks_backspace_ratio counts backspace and nothing else; folding Delete in with
# it would quietly redefine a feature that a live pattern is already built on.
_KEY_BACKSPACE = 14
_KEY_SPACE = 57

# evdev key codes by touch-typing hand and row (QWERTY). Ranges are inclusive.
_RANGES: tuple[tuple[int, int, int], ...] = (
    (2, 7, ZONE_LEFT_NUM),          # 1 2 3 4 5 6
    (8, 13, ZONE_RIGHT_NUM),        # 7 8 9 0 - =
    (16, 20, ZONE_LEFT_TOP),        # q w e r t
    (21, 27, ZONE_RIGHT_TOP),       # y u i o p [ ]
    (30, 34, ZONE_LEFT_HOME),       # a s d f g
    (35, 40, ZONE_RIGHT_HOME),      # h j k l ; '
    (44, 48, ZONE_LEFT_BOTTOM),     # z x c v b
    (49, 53, ZONE_RIGHT_BOTTOM),    # n m , . /
)

_SINGLES: dict[int, int] = {
    _KEY_BACKSPACE: ZONE_BACKSPACE,
    _KEY_SPACE: ZONE_SPACE,
    41: ZONE_LEFT_NUM,              # grave, left of the digit row
    43: ZONE_RIGHT_TOP,             # backslash
    29: ZONE_MODIFIER,              # left ctrl
    42: ZONE_MODIFIER,              # left shift
    54: ZONE_MODIFIER,              # right shift
    56: ZONE_MODIFIER,              # left alt
    58: ZONE_MODIFIER,              # caps lock
    97: ZONE_MODIFIER,              # right ctrl
    100: ZONE_MODIFIER,             # right alt
    125: ZONE_MODIFIER,             # left meta
    126: ZONE_MODIFIER,             # right meta
}

# A key held down while more than this many others are already held is not a
# human hand; the cap stops a stuck or synthetic stream growing the map without
# bound. Ten fingers plus modifiers leaves generous headroom.
_MAX_HELD = 64


def zone_of(ev_code: int) -> int:
    """The keyboard zone of an evdev key code. Never raises."""
    z = _SINGLES.get(ev_code)
    if z is not None:
        return z
    for lo, hi, zone in _RANGES:
        if lo <= ev_code <= hi:
            return zone
    return ZONE_OTHER


class KeyPseudonymiser:
    """Replaces key identity with (zone, pair id) on the way to disk.

    One instance per Writer, so the held-key map is per session and per process.
    A key still held when the daemon stops simply never gets its key-up, and the
    extractor skips an unpaired release exactly as it already does when a window
    starts mid-hold.
    """

    def __init__(self) -> None:
        self._held: dict[int, int] = {}      # ev_code -> pair id
        self._free: set[int] = set()
        self._next = 1

    def _acquire(self, code: int) -> int:
        pair = self._held.get(code)
        if pair is not None:                 # auto-repeat, or a repeated down
            return pair
        if len(self._held) >= _MAX_HELD:
            self._held.clear()               # not a hand; start over
            self._free.clear()
            self._next = 1
        pair = self._free.pop() if self._free else self._next
        if pair == self._next:
            self._next += 1
        self._held[code] = pair
        return pair

    def _release(self, code: int) -> int:
        pair = self._held.pop(code, None)
        if pair is None:
            return 0                         # a release with no press we saw
        self._free.add(pair)
        return pair

    def transform(self, row: tuple) -> tuple:
        """Map one collector row to its stored form.

        *row* is (ts_ns, ts_utc, session_id, dev_path, dev_name, dev_id,
        dev_type, ev_type, ev_code, ev_value); the result appends kb_zone and
        kb_pair. Only keyboard key events are rewritten — a mouse row keeps its
        ev_code, which carries the axis or button the mouse features read.
        """
        dev_type, ev_type, ev_code, ev_value = row[6], row[7], row[8], row[9]
        if dev_type != 'keyboard' or ev_type != 1:
            return row + (None, None)
        zone = zone_of(ev_code)
        pair = self._release(ev_code) if ev_value == 0 else self._acquire(ev_code)
        # ev_code is zeroed, not merely ignored: leaving it in place would keep
        # the keylog on disk no matter what the reader does with it. 0 is
        # KEY_RESERVED and is never emitted by a real device, so it also marks
        # the row as pseudonymised for the legacy-fallback path in
        # features/keystroke.py.
        return row[:8] + (0, ev_value, zone, pair)
