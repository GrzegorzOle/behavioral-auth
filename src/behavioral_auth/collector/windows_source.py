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


async def run_windows_hook(writer, session_id: str) -> None:
    """Hook the global keyboard and mouse, feeding events to *writer* forever."""
    from pynput import keyboard, mouse

    loop = asyncio.get_running_loop()
    shaper = _Shaper()

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

    kbd = keyboard.Listener(on_press=on_press, on_release=on_release)
    ms = mouse.Listener(on_move=on_move, on_click=on_click, on_scroll=on_scroll)
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
