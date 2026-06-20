"""Contextual feature extractor.

Encodes temporal and activity-density signals that help the model
distinguish normal work patterns from anomalous ones:
  - f_ctx_hour_sin    : sin of fractional hour (cyclic encoding)
  - f_ctx_hour_cos    : cos of fractional hour (cyclic encoding)
  - f_ctx_is_weekend  : 1.0 on Saturday/Sunday, else 0.0
  - f_activity_density: events per second in the window
"""

import math
import pandas as pd


def extract_context_features(ts_utc_series, total_events: int, window_sec: int) -> dict:
    """Compute 4 context features from the first timestamp of a window.

    Args:
        ts_utc_series: Series of UTC timestamps from the event DataFrame.
        total_events:  Total number of raw events in the window.
        window_sec:    Duration of the window in seconds.

    Returns:
        Dict with keys f_ctx_hour_sin, f_ctx_hour_cos, f_ctx_is_weekend,
        f_activity_density.
    """
    dt = pd.to_datetime(ts_utc_series.iloc[0], unit='s', utc=True)
    hour = dt.hour + dt.minute / 60.0
    angle = 2 * math.pi * (hour / 24.0)
    return {
        'f_ctx_hour_sin': float(math.sin(angle)), 'f_ctx_hour_cos': float(math.cos(angle)),
        'f_ctx_is_weekend': float(1 if dt.weekday() >= 5 else 0), 'f_activity_density': float(total_events / max(window_sec, 1))
    }
