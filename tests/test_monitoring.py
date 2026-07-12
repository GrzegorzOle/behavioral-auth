"""The alarm state machine.

Two behaviours here are load-bearing and easy to get wrong: an alarm needs a
*span* of anomalous behaviour rather than a burst of overlapping scores, and an
idle machine must hold its state instead of forgiving it.
"""

from __future__ import annotations

import pytest

import uuid

from behavioral_auth.daemon.monitoring import MonitorController
from behavioral_auth.inference.fusion import FaceState, Verdict, classify

SEC = 1_000_000_000


@pytest.fixture
def mon(cfg):
    cfg.alarm.enter_consecutive = 4
    cfg.alarm.enter_min_span_sec = 60
    cfg.alarm.clear_consecutive = 4
    cfg.alarm.clear_min_span_sec = 60
    cfg.face.stranger_consecutive = 3
    return MonitorController(cfg)


def _feed(mon, verdict, n, start_ns=0, step_sec=30):
    for i in range(n):
        mon.observe(verdict, 2.0 if verdict is Verdict.ANOMALOUS else 0.2,
                    start_ns + i * step_sec * SEC)


def test_a_burst_without_span_does_not_alarm(mon):
    """Adjacent sequences share most of their windows, so four scores one second
    apart are not four seconds of evidence — they are one moment seen four times."""
    _feed(mon, Verdict.ANOMALOUS, n=6, step_sec=1)     # 6 scores, ~5s of span

    assert mon.anom.count == 6
    assert mon.anom.span_sec < mon.cfg.alarm.enter_min_span_sec
    assert mon.should_raise() is None


def test_a_sustained_anomaly_alarms(mon):
    _feed(mon, Verdict.ANOMALOUS, n=5, step_sec=30)    # 5 scores across 120s

    assert mon.should_raise() == 'behavioral'


def test_idle_does_not_clear_an_alarm(mon, conn):
    """Walking away from the keyboard produces no scores. If the counters kept
    advancing on empty ticks, leaving the room would quietly forgive an intruder."""
    _feed(mon, Verdict.ANOMALOUS, n=5, step_sec=30)
    mon.raise_alarm(conn, str(uuid.uuid4()), str(uuid.uuid4()), 'behavioral')
    assert mon.alarm is not None

    for _ in range(50):                 # 50 ticks with no new sequence
        pass                            # observe() is simply never called

    assert mon.alarm is not None, 'the alarm evaporated while nobody was typing'
    assert not mon.should_clear()


def test_sustained_normal_clears_the_alarm(mon, conn):
    _feed(mon, Verdict.ANOMALOUS, n=5, step_sec=30)
    mon.raise_alarm(conn, str(uuid.uuid4()), str(uuid.uuid4()), 'behavioral')

    _feed(mon, Verdict.NORMAL, n=5, start_ns=10_000 * SEC, step_sec=30)

    assert mon.should_clear()
    mon.clear_alarm(conn)
    assert mon.alarm is None


def test_deadband_freezes_both_counters(mon):
    """A score hovering at the threshold is evidence of nothing and must not
    flap the alarm."""
    _feed(mon, Verdict.ANOMALOUS, n=3, step_sec=30)
    before = mon.anom.count

    mon.observe(Verdict.DEADBAND, 0.95, 9_999 * SEC)

    assert mon.anom.count == before      # not advanced...
    assert mon.norm.count == 0           # ...and not reset either


def test_an_anomaly_resets_the_normal_run(mon):
    _feed(mon, Verdict.NORMAL, n=3, step_sec=30)
    mon.observe(Verdict.ANOMALOUS, 2.0, 9_999 * SEC)

    assert mon.norm.count == 0
    assert mon.anom.count == 1


def test_a_stranger_at_the_camera_alarms_fast(mon):
    """The face channel does not need a span: seeing a different face is a
    discrete observation, not a trend."""
    for _ in range(3):
        mon.observe_face(FaceState.STRANGER)

    assert mon.should_raise() == 'face'


def test_an_unseen_face_is_never_evidence(mon):
    """A dark room, or a camera another app has grabbed, must not push the
    system toward an alarm. The old code fused a neutral 0.5 in and did."""
    for _ in range(20):
        mon.observe_face(FaceState.UNKNOWN)

    assert mon.face_stranger_streak == 0
    assert mon.should_raise() is None


def test_a_recognised_face_breaks_the_stranger_streak(mon):
    mon.observe_face(FaceState.STRANGER)
    mon.observe_face(FaceState.STRANGER)
    mon.observe_face(FaceState.MATCH)

    assert mon.face_stranger_streak == 0


# ── the classification rule itself ───────────────────────────────────────────

def test_classify_uses_a_deadband():
    assert classify(1.5, FaceState.MATCH, 0.2) is Verdict.ANOMALOUS
    assert classify(0.5, FaceState.MATCH, 0.2) is Verdict.NORMAL
    assert classify(0.9, FaceState.MATCH, 0.2) is Verdict.DEADBAND   # near the line


def test_classify_lets_the_face_channel_alarm_alone():
    """Behaviour can look perfectly normal while the wrong person sits there —
    an impostor who happens to type like you is still an impostor."""
    assert classify(0.1, FaceState.STRANGER, 0.2) is Verdict.ANOMALOUS


def test_classify_ignores_an_unknown_face():
    assert classify(0.1, FaceState.UNKNOWN, 0.2) is Verdict.NORMAL
