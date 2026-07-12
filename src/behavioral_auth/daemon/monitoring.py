"""The MONITORING controller: when does a run of odd scores become an alarm?

Two things make the naive rule ("N anomalous ticks in a row") wrong here.

First, consecutive sequences overlap heavily — with seq_len=12 and a 5 s
stride, neighbours share 11 of their 12 windows. A burst of anomalous scores
can therefore represent only a few seconds of genuinely new behaviour. So
entering an alarm requires both a count *and* a wall-clock span of new data.

Second, if the counters kept advancing while nobody was typing, walking away
from the desk would quietly clear an alarm. Scores only arrive when there is
activity, and the counters only move when a score arrives — so an unattended
machine holds its state instead of forgiving it.

Nothing in this module locks anything. On a sustained anomaly it writes a
warning and, at most, fires a desktop notification.
"""

from __future__ import annotations

import subprocess
import time
import uuid
from dataclasses import dataclass, field

from loguru import logger

from behavioral_auth.config import Settings
from behavioral_auth.inference.fusion import FaceState, Verdict


@dataclass
class Run:
    """A streak of same-verdict scores, tracked in event time."""
    count: int = 0
    first_ns: int = 0
    last_ns: int = 0

    def extend(self, seq_end_ns: int) -> None:
        if self.count == 0:
            self.first_ns = seq_end_ns
        self.count += 1
        self.last_ns = seq_end_ns

    def clear(self) -> None:
        self.count = 0
        self.first_ns = self.last_ns = 0

    @property
    def span_sec(self) -> float:
        return (self.last_ns - self.first_ns) / 1e9 if self.count else 0.0


@dataclass
class Alarm:
    alarm_id: str
    reason: str
    started_at: float
    peak_ratio: float = 0.0
    n_scores: int = 0
    last_notify_at: float = field(default=0.0)


class MonitorController:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.anom = Run()
        self.norm = Run()
        self.face_stranger_streak = 0
        self.alarm: Alarm | None = None
        self.recent_ratios: list[float] = []
        self.last_ratio: float | None = None
        self.face_state = FaceState.UNKNOWN

    def reset(self) -> None:
        self.anom.clear()
        self.norm.clear()
        self.face_stranger_streak = 0
        self.alarm = None
        self.recent_ratios.clear()
        self.last_ratio = None

    # ── face channel ──────────────────────────────────────────────────────

    def observe_face(self, state: FaceState) -> bool:
        """Record a face check. Returns True if it alone justifies an alarm."""
        self.face_state = state
        if state is FaceState.STRANGER:
            self.face_stranger_streak += 1
        elif state is FaceState.MATCH:
            self.face_stranger_streak = 0
        # UNKNOWN tells us nothing, so it neither builds nor breaks the streak.
        return self.face_stranger_streak >= self.cfg.face.stranger_consecutive

    # ── behavioural channel ───────────────────────────────────────────────

    def observe(self, verdict: Verdict, ratio: float, seq_end_ns: int) -> None:
        """Record one scored sequence. Only called when a new score exists."""
        self.last_ratio = ratio
        self.recent_ratios.append(round(ratio, 3))
        del self.recent_ratios[:-30]

        if verdict is Verdict.ANOMALOUS:
            self.anom.extend(seq_end_ns)
            self.norm.clear()
        elif verdict is Verdict.NORMAL:
            self.norm.extend(seq_end_ns)
            self.anom.clear()
        # DEADBAND: hold both runs — the score is too close to the threshold
        # to count as evidence either way.

        if self.alarm:
            self.alarm.n_scores += 1
            self.alarm.peak_ratio = max(self.alarm.peak_ratio, ratio)

    def should_raise(self) -> str | None:
        """The reason to enter ALARM, or None."""
        if self.alarm:
            return None
        if self.face_stranger_streak >= self.cfg.face.stranger_consecutive:
            return 'face'
        a = self.cfg.alarm
        if self.anom.count >= a.enter_consecutive and self.anom.span_sec >= a.enter_min_span_sec:
            return 'behavioral'
        return None

    def should_clear(self) -> bool:
        if not self.alarm:
            return False
        if self.face_stranger_streak >= self.cfg.face.stranger_consecutive:
            return False
        a = self.cfg.alarm
        return (self.norm.count >= a.clear_consecutive
                and self.norm.span_sec >= a.clear_min_span_sec)

    # ── alarm lifecycle ───────────────────────────────────────────────────

    def raise_alarm(self, conn, enrollment_id: str, session_id: str, reason: str) -> Alarm:
        self.alarm = Alarm(alarm_id=str(uuid.uuid4()), reason=reason, started_at=time.time())
        conn.execute(
            'INSERT INTO alarms (alarm_id, enrollment_id, session_id, reason, peak_ratio, n_scores) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            [self.alarm.alarm_id, enrollment_id, session_id, reason, self.last_ratio or 0.0, 0],
        )
        detail = (f'behaviour deviates from the learned pattern for '
                  f'{self.anom.span_sec:.0f}s (ratio up to {self.last_ratio:.2f}x threshold)'
                  if reason == 'behavioral'
                  else 'the camera does not recognise the person at the keyboard')
        logger.error(f'ALARM: {detail}. No action taken — this system never locks the session.')
        self.notify()
        return self.alarm

    def clear_alarm(self, conn) -> None:
        if not self.alarm:
            return
        conn.execute(
            'UPDATE alarms SET ended_at = now(), peak_ratio = ?, n_scores = ? WHERE alarm_id = ?',
            [self.alarm.peak_ratio, self.alarm.n_scores, self.alarm.alarm_id],
        )
        held = time.time() - self.alarm.started_at
        logger.info(f'Alarm cleared after {held:.0f}s — behaviour matches the pattern again')
        self.alarm = None

    def notify(self) -> None:
        """Fire the desktop notification, throttled. Never blocks the loop."""
        cmd = self.cfg.alarm.notify_cmd
        if not cmd or not self.alarm:
            return
        now = time.time()
        if now - self.alarm.last_notify_at < self.cfg.alarm.notify_cooldown_sec:
            return
        self.alarm.last_notify_at = now
        try:
            subprocess.Popen(cmd, shell=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            logger.warning(f'Notification failed: {exc}')

    def heartbeat(self) -> None:
        """Periodic reminder while an alarm is still standing."""
        if not self.alarm:
            return
        held = time.time() - self.alarm.started_at
        logger.warning(
            f'ALARM still active ({held:.0f}s, reason={self.alarm.reason}, '
            f'peak={self.alarm.peak_ratio:.2f}x)'
        )
        self.notify()
