"""Windows input source: a global keyboard + mouse hook via pynput.

The Linux collector reads evdev devices (collector/source.py :func:`run_evdev`);
this is its Windows counterpart. It writes the exact same event rows — same tuple
shape, same numeric codes (see collector/keycodes.py) — so everything downstream
(features, DuckDB, the model) is identical regardless of OS.

Three impedance mismatches with evdev are handled here:

  * pynput delivers events on its own listener threads, but :class:`Writer` is
    single-threaded and owned by the asyncio loop. Events are handed to the loop
    with ``call_soon_threadsafe`` and only ever written from the loop thread.
  * pynput does not flag auto-repeat, nor give relative mouse motion. :class:`_Shaper`
    holds the little state evdev provides for free (which keys are held, the last
    cursor position) to synthesise value 2 (auto-repeat) and REL_X/REL_Y deltas.
  * no kernel timestamps exist, so events are stamped with ``time.time_ns()`` at
    callback time — the closest Windows equivalent.

The event-shaping (:class:`_Shaper`) is pure and unit-tested. ``pynput`` is
imported lazily inside :func:`run_windows_hook`, so this module still imports on
Linux, where pynput is not installed.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from loguru import logger

from behavioral_auth.collector.keycodes import (
    BTN_LEFT,
    BTN_MIDDLE,
    BTN_RIGHT,
    EV_KEY,
    EV_REL,
    REL_WHEEL,
    REL_X,
    REL_Y,
    vk_to_evdev,
)
from behavioral_auth.collector.stack import WINDOWS_DEVICE_ID

_KBD_PATH, _MOUSE_PATH = '/windows/keyboard', '/windows/mouse'
_BUTTONS = {'left': BTN_LEFT, 'right': BTN_RIGHT, 'middle': BTN_MIDDLE}

# Bits the low-level hooks set on an event that came from SendInput rather than
# from a physical device. The mouse and keyboard structures do not agree on where
# the bit lives, which is why these are two separate masks and not one constant.
#   MSLLHOOKSTRUCT.flags:  LLMHF_INJECTED 0x01, LLMHF_LOWER_IL_INJECTED 0x02
#   KBDLLHOOKSTRUCT.flags: LLKHF_INJECTED 0x10, LLKHF_LOWER_IL_INJECTED 0x02
# The lower-integrity variant is counted as injected too: input synthesised by a
# *less* privileged process is more interesting here, not less.
_MOUSE_INJECTED_MASK = 0x01 | 0x02
_KBD_INJECTED_MASK = 0x10 | 0x02

# Below this many events a share means nothing — three injected events out of
# four is 75 % and is noise. Above it, the share is worth saying out loud.
INJECTION_MIN_SAMPLE = 500
INJECTION_WARN_SHARE = 0.20


class InjectionStats:
    """How much of the captured input says it was synthesised, not typed.

    Nothing in this project asked whether its input was *human* until now — an
    anti-idle jiggler supplied roughly two thirds of this machine's mouse events
    for days and the enrolment learned from it. Windows answers half of that
    question for free: both low-level hooks carry a flag saying the event came
    from ``SendInput``, and pynput passes the whole hook structure to an
    ``event_filter`` before dispatching. The flag was reaching Python already and
    was simply being dropped.

    It answers only half. A **hardware** jiggler on a USB port produces genuine
    HID events with the flag clear, so this cannot see one — that needs per-device
    identity, which means RawInput (``WM_INPUT``) and is not this class. The two
    detect different adversaries and neither subsumes the other.

    This counts and reports. It does not drop events and does not refuse to
    learn: accessibility tools, on-screen keyboards, remote-support software and
    KVM switches all inject legitimately, and silently discarding their input
    would blind the collector in exactly the situations a user most needs it.
    Consistent with the rest of the product, it warns.

    Pure — no pynput, no threads, no I/O — so it is unit-tested directly, like
    :class:`_Shaper`.
    """

    def __init__(self) -> None:
        self.keyboard_total = 0
        self.keyboard_injected = 0
        self.mouse_total = 0
        self.mouse_injected = 0

    def record_keyboard(self, flags: int) -> None:
        self.keyboard_total += 1
        if flags & _KBD_INJECTED_MASK:
            self.keyboard_injected += 1

    def record_mouse(self, flags: int) -> None:
        self.mouse_total += 1
        if flags & _MOUSE_INJECTED_MASK:
            self.mouse_injected += 1

    @staticmethod
    def _share(injected: int, total: int) -> float:
        return injected / total if total else 0.0

    @property
    def keyboard_share(self) -> float:
        return self._share(self.keyboard_injected, self.keyboard_total)

    @property
    def mouse_share(self) -> float:
        return self._share(self.mouse_injected, self.mouse_total)

    def loud_channels(self) -> list[str]:
        """Channels whose injected share is both large enough and sure enough.

        Both conditions matter. Without the sample floor the very first injected
        event reads as 100 % and the daemon would cry wolf on the first tick.
        """
        out = []
        for name, total, share in (
                ('keyboard', self.keyboard_total, self.keyboard_share),
                ('mouse', self.mouse_total, self.mouse_share)):
            if total >= INJECTION_MIN_SAMPLE and share >= INJECTION_WARN_SHARE:
                out.append(name)
        return out

    def as_dict(self) -> dict:
        return {
            'keyboard_total': self.keyboard_total,
            'keyboard_injected': self.keyboard_injected,
            'mouse_total': self.mouse_total,
            'mouse_injected': self.mouse_injected,
            'keyboard_share': round(self.keyboard_share, 4),
            'mouse_share': round(self.mouse_share, 4),
        }


class _Shaper:
    """Turn pynput-level facts into evdev ``(ev_type, code, value)`` triples.

    Holds the state evdev gives for free — which keys are held, the last cursor
    position — so it can synthesise auto-repeat and relative motion. No pynput,
    no threads, no I/O: unit-tested directly.
    """

    def __init__(self) -> None:
        self._held: set[int] = set()
        self._last: list[float | None] = [None, None]

    def key(self, vk: int, pressed: bool) -> tuple[int, int, int]:
        code = vk_to_evdev(vk)
        if pressed:
            value = 2 if vk in self._held else 1     # auto-repeat vs first press
            self._held.add(vk)
        else:
            self._held.discard(vk)
            value = 0
        return (EV_KEY, code, value)

    def move(self, x: float, y: float) -> list[tuple[int, int, int]]:
        px, py = self._last
        self._last = [x, y]
        if px is None:                               # first sample sets the origin
            return []
        out = []
        dx, dy = round(x - px), round(y - py)
        if dx:
            out.append((EV_REL, REL_X, dx))
        if dy:
            out.append((EV_REL, REL_Y, dy))
        return out

    def click(self, button_name: str, pressed: bool) -> tuple[int, int, int]:
        return (EV_KEY, _BUTTONS.get(button_name, BTN_LEFT), 1 if pressed else 0)

    def scroll(self, dy: float) -> tuple[int, int, int] | None:
        if not dy:
            return None
        return (EV_REL, REL_WHEEL, 1 if dy > 0 else -1)


def _extract_vk(key) -> int | None:
    """The Windows virtual-key code behind a pynput key, or None.

    Regular keys are ``KeyCode`` with ``.vk``; special keys are ``Key`` members
    wrapping a ``KeyCode`` in ``.value``.
    """
    vk = getattr(key, 'vk', None)
    if vk is None:
        vk = getattr(getattr(key, 'value', None), 'vk', None)
    return vk


async def run_windows_hook(writer, session_id: str,
                           stats: InjectionStats | None = None) -> None:
    """Hook the global keyboard and mouse, feeding events to *writer* forever.

    *stats*, if given, is filled in as events arrive so the daemon can report how
    much of the input claims to be synthetic. Optional so the hook stays usable
    without one.
    """
    from pynput import keyboard, mouse

    loop = asyncio.get_running_loop()
    shaper = _Shaper()
    stats = stats if stats is not None else InjectionStats()

    def emit(path, name, dev_type, triple, ts_ns=None) -> None:
        ev_type, code, value = triple
        # One physical event gets one timestamp. A move reports its two axes as
        # two rows, and evdev would give both the timestamp of the same SYN
        # frame; stamping them separately here put them microseconds apart and
        # left the feature extractor unable to tell which two rows were one
        # movement.
        ts_ns = time.time_ns() if ts_ns is None else ts_ns
        ts_utc = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
        # One global hook, no per-device identity: pynput cannot say which
        # keyboard produced a keystroke. Every event therefore claims the same
        # stack, which makes the stack gate inert on Windows rather than wrong.
        # Reaching real device identity there means RawInput (WM_INPUT), which
        # pynput does not expose.
        row = (ts_ns, ts_utc, session_id, path, name, WINDOWS_DEVICE_ID, dev_type,
               int(ev_type), int(code), int(value))
        # Cross the thread boundary: only the loop thread touches the Writer.
        loop.call_soon_threadsafe(writer.add, row)

    def _kbd(triple) -> None:
        emit(_KBD_PATH, 'windows-keyboard', 'keyboard', triple)

    def _mouse(triple, ts_ns=None) -> None:
        emit(_MOUSE_PATH, 'windows-mouse', 'mouse', triple, ts_ns)

    def on_press(key) -> None:
        vk = _extract_vk(key)
        if vk is not None:
            _kbd(shaper.key(vk, pressed=True))

    def on_release(key) -> None:
        vk = _extract_vk(key)
        if vk is not None:
            _kbd(shaper.key(vk, pressed=False))

    def on_move(x, y) -> None:
        ts_ns = time.time_ns()      # one movement, one timestamp, both axes
        for triple in shaper.move(x, y):
            _mouse(triple, ts_ns)

    def on_click(x, y, button, pressed) -> None:
        _mouse(shaper.click(getattr(button, 'name', ''), pressed))

    def on_scroll(x, y, dx, dy) -> None:
        triple = shaper.scroll(dy)
        if triple is not None:
            _mouse(triple)

    # pynput calls the filter with the raw hook structure *before* dispatching to
    # the callbacks above, on the same listener thread and in order — so the flags
    # recorded here belong to the event the next callback describes. One filter
    # per listener, never shared: the two listeners run on two threads.
    #
    # Returning False would SUPPRESS the event, swallowing the user's own
    # keystroke. These return None, deliberately and permanently.
    def _kbd_filter(msg, data) -> None:
        stats.record_keyboard(getattr(data, 'flags', 0))

    def _mouse_filter(msg, data) -> None:
        stats.record_mouse(getattr(data, 'flags', 0))

    # `win32_event_filter`, NOT `event_filter`. pynput namespaces backend options
    # by platform prefix (mouse/_base.py:253) and drops any keyword that does not
    # carry it — **silently**, with no error and a listener that still reports
    # running. The unprefixed name was measured here: 0 filter calls while input
    # kept flowing, i.e. a detector that detects nothing and says nothing. Pinned
    # by a test, because nothing else would catch it.
    kbd = keyboard.Listener(on_press=on_press, on_release=on_release,
                            win32_event_filter=_kbd_filter)
    ms = mouse.Listener(on_move=on_move, on_click=on_click, on_scroll=on_scroll,
                        win32_event_filter=_mouse_filter)
    kbd.start()
    ms.start()
    logger.info('Windows input hook active (keyboard + mouse via pynput)')
    try:
        # The listeners run on their own threads; keep this task alive until it
        # is cancelled (daemon shutdown) or a listener dies.
        while kbd.running and ms.running:
            await asyncio.sleep(1.0)
    finally:
        kbd.stop()
        ms.stop()
