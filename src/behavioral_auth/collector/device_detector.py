"""Input device detection utilities.

Identifies keyboard and mouse devices from the list of available evdev
devices.  Used by the collector to auto-discover devices when no explicit
paths are set in config.

This module is Linux-only — it needs ``evdev``, which does not exist on
Windows.  The import is deferred into the functions so that importing the
package (and the daemon, which imports :func:`detect_devices` at module top)
still works on a non-Linux box such as a Windows build host or CI. These
functions are simply never called there: the Windows collector uses a
different input backend. ``from __future__ import annotations`` keeps the
``evdev.InputDevice`` type hints from being evaluated at import time.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import evdev


@lru_cache(maxsize=1)
def _kbd_key_codes() -> frozenset:
    """Key codes that must be present for a device to count as a keyboard.

    Built lazily (and cached) rather than at import, so the module imports
    without evdev present.
    """
    from evdev import ecodes as e
    return frozenset({
        e.KEY_A, e.KEY_Z, e.KEY_ENTER, e.KEY_SPACE, e.KEY_BACKSPACE,
        e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT, e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL,
        e.KEY_LEFTALT, e.KEY_RIGHTALT, e.KEY_TAB, e.KEY_ESC,
        e.KEY_UP, e.KEY_DOWN, e.KEY_LEFT, e.KEY_RIGHT,
    })


def is_keyboard_device(dev: evdev.InputDevice) -> bool:
    """Return True if *dev* looks like a keyboard (has typical key codes)."""
    from evdev import ecodes
    caps = dev.capabilities(verbose=False)
    if ecodes.EV_KEY not in caps:
        return False
    return bool(set(caps[ecodes.EV_KEY]) & _kbd_key_codes())


def is_mouse_device(dev: evdev.InputDevice) -> bool:
    """Return True if *dev* generates relative movement events (mouse/trackpad)."""
    from evdev import ecodes
    return ecodes.EV_REL in dev.capabilities(verbose=False)


def detect_devices(configured=None) -> list[str]:
    """Return a list of device paths to monitor.

    If *configured* is non-empty, return it unchanged — this path needs no
    evdev, so an explicit device list works even where evdev is absent.
    Otherwise auto-discover all keyboard and mouse devices (Linux only).
    """
    if configured:
        return configured
    import evdev
    devs = []
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        if is_keyboard_device(dev) or is_mouse_device(dev):
            devs.append(path)
    return devs
