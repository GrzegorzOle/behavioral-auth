"""Input sources: real evdev devices, and a synthetic generator for testing.

The synthetic source exists because the alternative way to test the
LEARNING → MONITORING → ALARM path is to type for several hours and then
persuade someone else to sit down at your keyboard. It emits evdev-shaped
events on a *simulated* clock, so an hour of activity can be produced in a
minute — the feature pipeline derives everything from event timestamps, never
from wall-clock time, which is what makes that sound.

It is refused outright in prod mode: nothing may inject fake behaviour into a
pattern that is meant to identify a real person.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
from loguru import logger

EV_KEY, EV_REL = 1, 2
REL_X, REL_Y, REL_WHEEL = 0, 1, 8
BTN_LEFT = 272
KEY_BACKSPACE = 14


async def run_evdev(path: str, writer, session_id: str) -> None:
    """Read one evdev device forever, feeding events to *writer*."""
    import evdev

    from behavioral_auth.collector.device_detector import is_keyboard_device

    from behavioral_auth.collector.stack import device_id

    dev = evdev.InputDevice(path)
    dev_type = 'keyboard' if is_keyboard_device(dev) else 'mouse'
    # vendor:product, not the path: /dev/input/eventN renumbers across boots and
    # re-plugs, so it cannot identify the same keyboard after a dock cycle.
    dev_id = device_id(dev.info.vendor, dev.info.product)
    logger.info(f'Reading {path} ({dev.name}, {dev_id}) as {dev_type}')

    async for ev in dev.async_read_loop():
        if ev.type not in (evdev.ecodes.EV_KEY, evdev.ecodes.EV_REL,
                           evdev.ecodes.EV_ABS, evdev.ecodes.EV_MSC):
            continue
        # Kernel timestamps, not read-time wall clock: a GIL stall in a worker
        # thread cannot skew a single feature.
        ts_ns = ev.sec * 1_000_000_000 + ev.usec * 1_000
        ts_utc = datetime.fromtimestamp(ev.sec + ev.usec / 1e6, tz=timezone.utc)
        writer.add((ts_ns, ts_utc, session_id, path, dev.name, dev_id, dev_type,
                    ev.type, ev.code, ev.value))


@dataclass
class Profile:
    """A synthetic person's motor characteristics."""
    name: str
    dwell_ms: float          # how long a key is held
    dwell_spread: float
    flight_ms: float         # gap between successive key presses
    flight_spread: float
    mouse_speed: float       # px per movement event
    mouse_jitter: float
    burst_keys: int          # keys per typing burst

    @staticmethod
    def named(name: str) -> 'Profile':
        if name == 'user':
            return Profile('user', dwell_ms=95, dwell_spread=0.28,
                           flight_ms=170, flight_spread=0.45,
                           mouse_speed=9.0, mouse_jitter=2.5, burst_keys=14)
        if name == 'impostor':
            # Deliberately a different motor signature: faster, tighter,
            # jerkier mouse. Not a caricature — a plausible other person.
            return Profile('impostor', dwell_ms=52, dwell_spread=0.5,
                           flight_ms=88, flight_spread=0.8,
                           mouse_speed=22.0, mouse_jitter=9.0, burst_keys=26)
        raise ValueError(f'unknown profile: {name!r}')


class SyntheticSource:
    """Emit synthetic keyboard and mouse events on an accelerated clock."""

    def __init__(self, writer, session_id: str, profile: str = 'user',
                 speed: float = 20.0, chunk_sec: float = 5.0, seed: int = 0):
        self.writer = writer
        self.session_id = session_id
        self.profile = Profile.named(profile)
        self.speed = speed
        self.chunk_sec = chunk_sec
        self.rng = np.random.default_rng(seed)
        self.now_ns = time.time_ns()

    def set_profile(self, name: str) -> None:
        """Swap the person at the keyboard mid-run (the impostor test)."""
        self.profile = Profile.named(name)
        logger.warning(f'Synthetic profile switched to {name!r}')

    def _lognormal(self, median_ms: float, spread: float) -> float:
        return float(self.rng.lognormal(np.log(median_ms), spread))

    def _chunk(self) -> list[tuple]:
        """Generate one chunk_sec slice of simulated activity."""
        p = self.profile
        rows: list[tuple] = []
        end_ns = self.now_ns + int(self.chunk_sec * 1e9)
        t = self.now_ns

        def emit(ts_ns, dev_type, ev_type, code, value):
            rows.append((
                int(ts_ns),
                datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc),
                self.session_id,
                f'/synthetic/{dev_type}', f'synthetic-{p.name}',
                # Keyed on the modality, NOT on the profile: `set-profile` swaps
                # the synthetic person mid-run, and that is a change of person,
                # not a change of hardware. Keying on the profile would make the
                # impostor arrive on an unknown stack, so the stack gate would
                # suspend scoring and the demo would never reach ALARM.
                f'synthetic:{dev_type}', dev_type,
                ev_type, code, int(value),
            ))

        while t < end_ns:
            if self.rng.random() < 0.6:
                # A typing burst.
                for _ in range(self.rng.poisson(p.burst_keys) + 1):
                    code = (KEY_BACKSPACE if self.rng.random() < 0.06
                            else int(self.rng.integers(16, 50)))
                    dwell = self._lognormal(p.dwell_ms, p.dwell_spread) * 1e6
                    emit(t, 'keyboard', EV_KEY, code, 1)
                    emit(t + dwell, 'keyboard', EV_KEY, code, 0)
                    t += int(dwell + self._lognormal(p.flight_ms, p.flight_spread) * 1e6)
                    if t >= end_ns:
                        break
            else:
                # A mouse movement burst, occasionally clicking or scrolling.
                for _ in range(int(self.rng.integers(8, 30))):
                    dx = self.rng.normal(0, p.mouse_speed) + self.rng.normal(0, p.mouse_jitter)
                    dy = self.rng.normal(0, p.mouse_speed) + self.rng.normal(0, p.mouse_jitter)
                    emit(t, 'mouse', EV_REL, REL_X, round(dx))
                    emit(t, 'mouse', EV_REL, REL_Y, round(dy))
                    t += int(abs(self.rng.normal(12, 4)) * 1e6)
                    if t >= end_ns:
                        break
                if self.rng.random() < 0.35:
                    hold = abs(self.rng.normal(80, 25)) * 1e6
                    emit(t, 'mouse', EV_KEY, BTN_LEFT, 1)
                    emit(t + hold, 'mouse', EV_KEY, BTN_LEFT, 0)
                    t += int(hold + 40e6)
                if self.rng.random() < 0.2:
                    emit(t, 'mouse', EV_REL, REL_WHEEL, self.rng.choice([-1, 1]))
                    t += int(30e6)

        self.now_ns = end_ns
        return sorted(rows, key=lambda r: r[0])

    async def run(self) -> None:
        logger.warning(
            f'SYNTHETIC INPUT ACTIVE (profile={self.profile.name}, {self.speed}x) '
            f'— this is test data, not a real person'
        )
        while True:
            for row in self._chunk():
                self.writer.add(row)
            await asyncio.sleep(self.chunk_sec / self.speed)
