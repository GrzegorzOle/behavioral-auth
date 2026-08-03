"""Mouse dynamics feature extractor.

Computes 9 features from a window of raw mouse events:
  - f_ms_count        : number of relative-movement samples
  - f_ms_speed_mean   : mean cursor speed (pixels/s)
  - f_ms_speed_std    : std dev of cursor speed
  - f_ms_acc_mean     : mean acceleration magnitude
  - f_ms_clicks       : number of completed left-click press-release pairs
  - f_ms_click_dwell  : mean click hold duration (ms)
  - f_ms_scrolls      : number of scroll wheel events
  - f_ms_idle_ratio   : fraction of time the cursor is nearly stationary
  - f_ms_curvature    : mean absolute direction change between moves

The two axes arrive as *separate events* and an axis that did not move emits
nothing at all — evdev's convention, mirrored by the Windows shaper. So REL_X
and REL_Y are two streams of unequal length and cannot be zipped by position;
they have to be regrouped into motion samples first (:func:`_motion_samples`).

Their values are *deltas*, not positions. The distance covered by one sample is
therefore ``hypot(dx, dy)`` directly — differencing them again would yield the
change in velocity and call it distance.
"""

import numpy as np

# Two axes of one physical movement are emitted back to back and land
# microseconds apart; consecutive movements are milliseconds apart even at a
# 1000 Hz polling rate. Anything inside this gap is one sample.
_SAME_SAMPLE_NS = 1_000_000        # 1 ms


def _motion_samples(rel) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Regroup per-axis relative events into (ts, dx, dy) motion samples.

    A sample ends when the time gap exceeds :data:`_SAME_SAMPLE_NS` or when an
    axis repeats, since one movement reports each axis at most once. A missing
    axis is a real zero — the device said it did not move — not missing data.
    """
    ts_all = rel['ts_ns'].to_numpy()
    code_all = rel['ev_code'].to_numpy()
    val_all = rel['ev_value'].to_numpy()

    ts: list[int] = []
    dx: list[float] = []
    dy: list[float] = []
    cur_ts: int | None = None
    cur = {}

    for t, c, v in zip(ts_all, code_all, val_all):
        new_sample = (
            cur_ts is None
            or t - cur_ts > _SAME_SAMPLE_NS
            or c in cur
        )
        if new_sample:
            if cur_ts is not None:
                ts.append(cur_ts)
                dx.append(cur.get(0, 0.0))
                dy.append(cur.get(1, 0.0))
            cur_ts, cur = int(t), {}
        cur[int(c)] = float(v)

    if cur_ts is not None:
        ts.append(cur_ts)
        dx.append(cur.get(0, 0.0))
        dy.append(cur.get(1, 0.0))

    return (np.asarray(ts, dtype=np.float64),
            np.asarray(dx, dtype=np.float64),
            np.asarray(dy, dtype=np.float64))


def extract_mouse_features(df) -> dict | None:
    """Extract mouse dynamics from a DataFrame of mixed device events.

    Args:
        df: DataFrame slice for the current window (all device types).
            Mouse rows are filtered internally by dev_type == 'mouse'.

    Returns:
        Dict of 9 float features, or None if the window is too sparse.
    """
    if df.empty:
        return None
    m = df[df.dev_type == 'mouse'].copy()
    # Motion only. REL_WHEEL is also ev_type 2 and would otherwise be read as a
    # movement with no axis, dragging a bogus timestamp into the speed series.
    rel = m[(m.ev_type == 2) & (m.ev_code.isin((0, 1)))].sort_values('ts_ns')
    if rel.empty:
        return None
    ts, dx, dy = _motion_samples(rel)
    if len(ts) < 3:
        return None
    # dt spans consecutive samples, so it pairs with dx/dy from the second
    # sample onward.
    dt = np.maximum(np.diff(ts) / 1e9, 1e-6)
    dist = np.hypot(dx[1:], dy[1:])
    speed = dist / dt
    acc = np.diff(speed) / np.maximum(dt[:-1], 1e-6) if len(speed) > 1 else np.array([0.0])
    angles = np.arctan2(dy[1:], dx[1:])
    curv = np.abs(np.diff(angles)) if len(angles) > 1 else np.array([0.0])
    curv = np.minimum(curv, 2*np.pi - curv)
    clicks = m[(m.ev_type == 1) & (m.ev_code == 272)]
    click_dwell = []
    down = None
    for r in clicks.itertuples(index=False):
        if r.ev_value == 1:
            down = r.ts_ns
        elif r.ev_value == 0 and down is not None:
            click_dwell.append((r.ts_ns - down) / 1e6)
            down = None
    return {
        'f_ms_count': float(len(ts)), 'f_ms_speed_mean': float(np.mean(speed)), 'f_ms_speed_std': float(np.std(speed)),
        'f_ms_acc_mean': float(np.mean(acc)), 'f_ms_clicks': float(len(click_dwell)),
        'f_ms_click_dwell': float(np.mean(click_dwell)) if click_dwell else 0.0,
        'f_ms_scrolls': float(len(m[(m.ev_type == 2) & (m.ev_code == 8)])),
        'f_ms_idle_ratio': float(np.mean(speed < 2.0)), 'f_ms_curvature': float(np.mean(curv)) if len(curv) else 0.0
    }
