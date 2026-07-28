"""Windows virtual-key codes -> Linux evdev key codes.

The whole feature pipeline and the DuckDB schema speak evdev's numeric code
space (see collector/source.py, features/keystroke.py, features/mouse.py). The
Windows collector reads Windows virtual-key (VK) codes instead, so it must
translate them into that same space before anything is written — otherwise a
pattern learned on Windows would live in a different coordinate system than the
one every feature and query assumes.

Only two things actually have to be right for the features to mean what they
mean on Linux:

  * backspace must land on evdev KEY_BACKSPACE (14) — features/keystroke.py
    counts it by that literal code for the backspace ratio;
  * every physical key must get a *stable, distinct* code, so dwell can pair a
    press with its release and the entropy/repeat features see the real key
    distribution.

Mapping the rest to their true evdev codes as well is not required, but it is
cheap and keeps Windows-captured data in the same coordinate system as Linux,
which is one less surprise. Anything not in the table falls back to 1000+vk: VK
codes are < 256 and evdev key codes are < 768 (KEY_MAX 0x2ff), so that range can
never collide with a real evdev code or with another VK.

This module is pure integer arithmetic on purpose — no pynput, no Windows API —
so it imports and is unit-tested on any platform.
"""

from __future__ import annotations

# evdev KEY_* codes (from linux/input-event-codes.h), by the physical key.
_KEY_ESC, _KEY_BACKSPACE, _KEY_TAB, _KEY_ENTER, _KEY_SPACE = 1, 14, 15, 28, 57
_KEY_LEFTCTRL, _KEY_LEFTSHIFT, _KEY_LEFTALT = 29, 42, 56
_KEY_RIGHTSHIFT, _KEY_RIGHTCTRL, _KEY_RIGHTALT = 54, 97, 100
_KEY_CAPSLOCK = 58
_KEY_UP, _KEY_DOWN, _KEY_LEFT, _KEY_RIGHT = 103, 108, 105, 106
_KEY_HOME, _KEY_END, _KEY_PAGEUP, _KEY_PAGEDOWN = 102, 107, 104, 109
_KEY_INSERT, _KEY_DELETE = 110, 111

# Letters A-Z in evdev's (layout-ordered, not alphabetical) numbering.
_LETTERS = {
    'A': 30, 'B': 48, 'C': 46, 'D': 32, 'E': 18, 'F': 33, 'G': 34, 'H': 35,
    'I': 23, 'J': 36, 'K': 37, 'L': 38, 'M': 50, 'N': 49, 'O': 24, 'P': 25,
    'Q': 16, 'R': 19, 'S': 31, 'T': 20, 'U': 22, 'V': 47, 'W': 17, 'X': 45,
    'Y': 21, 'Z': 44,
}
# Digit row 1-9 then 0 (evdev KEY_1..KEY_0 = 2..11).
_DIGITS = {'1': 2, '2': 3, '3': 4, '4': 5, '5': 6, '6': 7, '7': 8, '8': 9,
           '9': 10, '0': 11}

# Windows virtual-key code -> evdev code, for the keys with a fixed VK.
_VK_MAP: dict[int, int] = {
    0x08: _KEY_BACKSPACE, 0x09: _KEY_TAB, 0x0D: _KEY_ENTER, 0x1B: _KEY_ESC,
    0x20: _KEY_SPACE,
    0x10: _KEY_LEFTSHIFT, 0x11: _KEY_LEFTCTRL, 0x12: _KEY_LEFTALT,
    0xA0: _KEY_LEFTSHIFT, 0xA1: _KEY_RIGHTSHIFT,      # L/R shift
    0xA2: _KEY_LEFTCTRL, 0xA3: _KEY_RIGHTCTRL,        # L/R ctrl
    0xA4: _KEY_LEFTALT, 0xA5: _KEY_RIGHTALT,          # L/R alt (menu)
    0x14: _KEY_CAPSLOCK,
    0x25: _KEY_LEFT, 0x26: _KEY_UP, 0x27: _KEY_RIGHT, 0x28: _KEY_DOWN,
    0x24: _KEY_HOME, 0x23: _KEY_END, 0x21: _KEY_PAGEUP, 0x22: _KEY_PAGEDOWN,
    0x2D: _KEY_INSERT, 0x2E: _KEY_DELETE,
}
# Letters: VK 0x41-0x5A are ASCII 'A'-'Z'. Digits: VK 0x30-0x39 are '0'-'9'.
_VK_MAP.update({0x41 + i: _LETTERS[chr(ord('A') + i)] for i in range(26)})
_VK_MAP.update({ord(d): code for d, code in _DIGITS.items()})

# Mouse (evdev): relative axes and buttons the mouse features read.
REL_X, REL_Y, REL_WHEEL = 0, 1, 8
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 272, 273, 274

EV_KEY, EV_REL = 1, 2


def vk_to_evdev(vk: int) -> int:
    """Translate a Windows virtual-key code to an evdev key code.

    Unmapped keys get a stable, collision-free 1000+vk code (see module docstring).
    """
    code = _VK_MAP.get(vk)
    if code is not None:
        return code
    return 1000 + vk
