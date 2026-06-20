import evdev
KBD_KEY_CODES = {
    evdev.ecodes.KEY_A, evdev.ecodes.KEY_Z, evdev.ecodes.KEY_ENTER,
    evdev.ecodes.KEY_SPACE, evdev.ecodes.KEY_BACKSPACE, evdev.ecodes.KEY_LEFTSHIFT,
    evdev.ecodes.KEY_RIGHTSHIFT, evdev.ecodes.KEY_LEFTCTRL, evdev.ecodes.KEY_RIGHTCTRL,
    evdev.ecodes.KEY_LEFTALT, evdev.ecodes.KEY_RIGHTALT, evdev.ecodes.KEY_TAB,
    evdev.ecodes.KEY_ESC, evdev.ecodes.KEY_UP, evdev.ecodes.KEY_DOWN,
    evdev.ecodes.KEY_LEFT, evdev.ecodes.KEY_RIGHT
}

def is_keyboard_device(dev: evdev.InputDevice) -> bool:
    caps = dev.capabilities(verbose=False)
    if evdev.ecodes.EV_KEY not in caps:
        return False
    return bool(set(caps[evdev.ecodes.EV_KEY]) & KBD_KEY_CODES)

def is_mouse_device(dev: evdev.InputDevice) -> bool:
    return evdev.ecodes.EV_REL in dev.capabilities(verbose=False)

def detect_devices(configured=None):
    if configured:
        return configured
    devs = []
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        if is_keyboard_device(dev) or is_mouse_device(dev):
            devs.append(path)
    return devs
