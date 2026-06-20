"""Mouse dynamics feature extractor.

Computes 9 features from a window of raw mouse events:
  - f_ms_count        : number of relative-movement events
  - f_ms_speed_mean   : mean cursor speed (pixels/s)
  - f_ms_speed_std    : std dev of cursor speed
  - f_ms_acc_mean     : mean acceleration magnitude
  - f_ms_clicks       : number of completed left-click press-release pairs
  - f_ms_click_dwell  : mean click hold duration (ms)
  - f_ms_scrolls      : number of scroll wheel events
  - f_ms_idle_ratio   : fraction of time the cursor is nearly stationary
  - f_ms_curvature    : mean absolute direction change between moves
"""

import numpy as np


def extract_mouse_features(df) -> dict | None:
    """Extract mouse dynamics from a DataFrame of mixed device events.

    Args:
        df: DataFrame slice for the current window (all device types).
            Mouse rows are filtered internally by dev_type == 'mouse'.

    Returns:
        Dict of 9 float features, or None if the window is too sparse.
    """
    m = df[df.dev_type == 'mouse'].copy(); rel = m[m.ev_type == 2]
    if rel.empty or len(rel) < 3:
        return None
    x = rel[rel.ev_code == 0]['ev_value'].to_numpy(); y = rel[rel.ev_code == 1]['ev_value'].to_numpy(); ts = rel['ts_ns'].to_numpy()
    n = min(len(x), len(y), len(ts))
    if n < 3:
        return None
    x, y, ts = x[:n], y[:n], ts[:n]
    dt = np.maximum(np.diff(ts) / 1e9, 1e-6)
    dx = np.diff(x); dy = np.diff(y)
    speed = np.hypot(dx, dy) / dt
    acc = np.diff(speed) / np.maximum(dt[:-1], 1e-6) if len(speed) > 1 else np.array([0.0])
    angles = np.arctan2(dy, dx); curv = np.abs(np.diff(angles)) if len(angles) > 1 else np.array([0.0]); curv = np.minimum(curv, 2*np.pi-curv)
    clicks = m[(m.ev_type == 1) & (m.ev_code == 272)]
    click_dwell = []; down = None
    for r in clicks.itertuples(index=False):
        if r.ev_value == 1:
            down = r.ts_ns
        elif r.ev_value == 0 and down is not None:
            click_dwell.append((r.ts_ns - down) / 1e6); down = None
    return {
        'f_ms_count': float(len(rel)), 'f_ms_speed_mean': float(np.mean(speed)), 'f_ms_speed_std': float(np.std(speed)),
        'f_ms_acc_mean': float(np.mean(acc)), 'f_ms_clicks': float(len(click_dwell)),
        'f_ms_click_dwell': float(np.mean(click_dwell)) if click_dwell else 0.0,
        'f_ms_scrolls': float(len(m[(m.ev_type == 2) & (m.ev_code == 8)])),
        'f_ms_idle_ratio': float(np.mean(speed < 2.0)), 'f_ms_curvature': float(np.mean(curv)) if len(curv) else 0.0
    }
