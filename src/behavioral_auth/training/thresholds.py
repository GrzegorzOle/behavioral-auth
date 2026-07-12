"""Anomaly threshold calibration.

Calibrated on the *holdout* — data the model never trained on. Calibrating on
the training split would measure how well the model memorised, not how it
behaves on data it has not seen.

Not a raw percentile, though. A p99 taken over a few hundred sequences is
effectively "the second largest value I happened to see", which jumps around
wildly between cycles and makes the threshold look unstable when the model is
fine. Reconstruction errors are positive and heavy-tailed, so we work in log
space and use a median-plus-MAD estimator: robust to the handful of outliers
that dominate a tail percentile, and steady enough that a moving threshold
means the *pattern* is really still moving.
"""

from __future__ import annotations

import numpy as np

MAD_TO_SIGMA = 1.4826   # makes MAD a consistent estimator of sigma for a normal


def calibrate_from_holdout(errors: np.ndarray, k: float = 4.0) -> float:
    """Anomaly threshold: *k* robust deviations above the typical error.

    Args:
        errors: Per-sequence reconstruction errors from the holdout.
        k:      How many robust standard deviations above the median counts as
                anomalous. Higher = fewer false alarms on the user themselves.
    """
    if len(errors) == 0:
        raise ValueError('cannot calibrate a threshold from an empty holdout')

    log_err = np.log(np.maximum(errors, 1e-12))
    median = float(np.median(log_err))
    mad = float(np.median(np.abs(log_err - median)))

    if mad <= 0:   # degenerate: every error identical
        return float(np.max(errors) * 1.5)

    return float(np.exp(median + k * MAD_TO_SIGMA * mad))


def false_alarm_rate(errors: np.ndarray, threshold: float) -> float:
    """Fraction of the user's own held-out data this threshold would flag.

    This is the one error rate we *can* honestly measure: how often the system
    would cry wolf about its own owner. (The other direction — how often it
    would accept an impostor — is unmeasurable here; there are no impostors.)
    """
    if len(errors) == 0:
        return 0.0
    return float(np.mean(errors > threshold))


def shape_ratio(errors: np.ndarray, threshold: float) -> float:
    """Threshold expressed as a multiple of the typical error.

    Scale-free, and that is the point. Every cycle retrains from a fresh random
    initialisation, so the *absolute* reconstruction error lands on a different
    scale each time — comparing raw thresholds between cycles measures init
    noise, not whether the pattern has settled. This ratio depends only on the
    spread of the error distribution, so when it stops moving, the pattern
    really has stopped moving.
    """
    med = float(np.median(errors))
    return threshold / med if med > 0 else 0.0
