"""Combining the behavioural and face channels.

These two signals fail in completely different ways, so they are kept as
independent channels and combined by a rule, not by averaging: an alarm is
raised when behaviour looks wrong OR the camera sees someone else. Averaging
them would let a dark room (no face) degrade the behavioural detector, and
would let one weak channel dilute a strong signal from the other.

`display_score` exists only so the console has a single number to show. It is
not the decision rule.
"""

from __future__ import annotations

from enum import Enum


class FaceState(str, Enum):
    MATCH = 'match'
    STRANGER = 'stranger'
    UNKNOWN = 'unknown'   # no face, camera busy, no model — never evidence


class Verdict(str, Enum):
    NORMAL = 'normal'
    ANOMALOUS = 'anomalous'
    DEADBAND = 'deadband'  # between the two: counters hold, nothing decided


def classify(ratio: float, face: FaceState, clear_hysteresis: float) -> Verdict:
    """Classify one scored sequence.

    *ratio* is the reconstruction error over the calibrated threshold, so 1.0
    is exactly the threshold. Clearing requires dropping meaningfully below it
    — the gap between the two is a deadband where neither counter moves, which
    is what stops a score hovering at the threshold from flapping the alarm.
    """
    if ratio > 1.0 or face is FaceState.STRANGER:
        return Verdict.ANOMALOUS
    if ratio < 1.0 - clear_hysteresis and face is not FaceState.STRANGER:
        return Verdict.NORMAL
    return Verdict.DEADBAND


def display_score(ratio: float, face: FaceState) -> float:
    """A single 0-1 number for the console. Display only."""
    beh = min(ratio, 2.0) / 2.0
    if face is FaceState.UNKNOWN:
        return beh
    face_score = 0.05 if face is FaceState.MATCH else 0.85
    return 0.7 * beh + 0.3 * face_score
