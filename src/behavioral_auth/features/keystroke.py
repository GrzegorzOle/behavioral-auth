"""Keystroke dynamics feature extractor.

Computes 8 features from a window of raw keyboard events:
  - f_ks_count         : total key events in the window
  - f_ks_mean_dwell    : mean key-hold duration (ms)
  - f_ks_std_dwell     : std dev of key-hold duration
  - f_ks_mean_flight   : mean inter-key interval (ms)
  - f_ks_std_flight    : std dev of inter-key interval
  - f_ks_backspace_ratio : fraction of backspace presses
  - f_ks_repeat_ratio  : fraction of auto-repeat events
  - f_ks_entropy       : Shannon entropy of dwell-time histogram
"""

import numpy as np


def extract_keystroke_features(df) -> dict | None:
    """Extract keystroke dynamics from a DataFrame of keyboard events.

    Args:
        df: DataFrame slice containing only keyboard device rows,
            with columns [ts_ns, ev_type, ev_code, ev_value].

    Returns:
        Dict of 8 float features, or None if the window is too sparse.
    """
    if df.empty:
        return None
    k = df[df.ev_type == 1].copy()
    if k.empty:
        return None
    downs, dwell, flight = {}, [], []
    last_down = None; backspace = 0; repeat = 0; total = 0
    for r in k.itertuples(index=False):
        total += 1
        if r.ev_value == 2:
            repeat += 1
        if r.ev_value == 1:
            if last_down is not None:
                flight.append((r.ts_ns - last_down) / 1e6)
            downs[r.ev_code] = r.ts_ns
            last_down = r.ts_ns
        elif r.ev_value == 0 and r.ev_code in downs:
            dwell.append((r.ts_ns - downs.pop(r.ev_code)) / 1e6)
        if r.ev_code == 14:
            backspace += 1
    if len(dwell) < 2:
        return None
    h = np.histogram(np.array(dwell), bins=8, density=True)[0] + 1e-9
    return {
        'f_ks_count': float(len(k)), 'f_ks_mean_dwell': float(np.mean(dwell)), 'f_ks_std_dwell': float(np.std(dwell)),
        'f_ks_mean_flight': float(np.mean(flight)) if flight else 0.0, 'f_ks_std_flight': float(np.std(flight)) if flight else 0.0,
        'f_ks_backspace_ratio': float(backspace / max(total, 1)), 'f_ks_repeat_ratio': float(repeat / max(total, 1)),
        'f_ks_entropy': float(-np.sum(h * np.log(h)))
    }
