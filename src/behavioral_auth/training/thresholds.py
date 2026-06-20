"""Anomaly threshold calculation from validation reconstruction errors.

The thresholds are percentiles of per-sample MSE on the validation set:
  - challenge_threshold: 95th percentile → flag as suspicious
  - lock_threshold:      99th percentile → trigger lock action
"""

import numpy as np


def calculate_thresholds(errors: 'np.ndarray') -> tuple[float, float]:
    """Return (challenge_threshold, lock_threshold) from validation errors.

    Args:
        errors: 1-D array of per-sample MSE values from the validation set.

    Returns:
        Tuple (p95, p99) as floats.
    """
    return float(np.percentile(errors, 95)), float(np.percentile(errors, 99))
