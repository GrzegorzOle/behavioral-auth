import math
import pandas as pd

def extract_context_features(ts_utc_series, total_events, window_sec):
    dt = pd.to_datetime(ts_utc_series.iloc[0], unit='s', utc=True)
    hour = dt.hour + dt.minute / 60.0
    angle = 2 * math.pi * (hour / 24.0)
    return {
        'f_ctx_hour_sin': float(math.sin(angle)), 'f_ctx_hour_cos': float(math.cos(angle)),
        'f_ctx_is_weekend': float(1 if dt.weekday() >= 5 else 0), 'f_activity_density': float(total_events / max(window_sec, 1))
    }
