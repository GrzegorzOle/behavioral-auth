"""Input device detection utilities.

Identifies keyboard and mouse devices from the list of available evdev
devices.  Used by the collector to auto-discover devices when no explicit
paths are set in config.
"""

import evdev

# Key codes that must be present for a device to be classified as a keyboard
KBD_KEY_CODES = {
    evdev.ecodes.KEY_A, evdev.ecodes.KEY_Z, evdev.ecodes.KEY_ENTER,
    evdev.ecodes.KEY_SPACE, evdev.ecodes.KEY_BACKSPACE, evdev.ecodes.KEY_LEFTSHIFT,
    evdev.ecodes.KEY_RIGHTSHIFT, evdev.ecodes.KEY_LEFTCTRL, evdev.ecodes.KEY_RIGHTCTRL,
    evdev.ecodes.KEY_LEFTALT, evdev.ecodes.KEY_RIGHTALT, evdev.ecodes.KEY_TAB,
    evdev.ecodes.KEY_ESC, evdev.ecodes.KEY_UP, evdev.ecodes.KEY_DOWN,
    evdev.ecodes.KEY_LEFT, evdev.ecodes.KEY_RIGHT
}

def is_keyboard_device(dev: evdev.InputDevice) -> bool:
    """Return True if *dev* looks like a keyboard (has typical key codes)."""
    caps = dev.capabilities(verbose=False)
    if evdev.ecodes.EV_KEY not in caps:
        return False
    return bool(set(caps[evdev.ecodes.EV_KEY]) & KBD_KEY_CODES)

def is_mouse_device(dev: evdev.InputDevice) -> bool:
    """Return True if *dev* generates relative movement events (mouse/trackpad)."""
    return evdev.ecodes.EV_REL in dev.capabilities(verbose=False)

def detect_devices(configured=None) -> list[str]:
    """Return a list of device paths to monitor.

    If *configured* is non-empty, return it unchanged.
    Otherwise auto-discover all keyboard and mouse devices.
    """
    if configured:
        return configured
    devs = []
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        if is_keyboard_device(dev) or is_mouse_device(dev):
            devs.append(path)
    return devs
