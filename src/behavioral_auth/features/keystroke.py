import numpy as np

def extract_keystroke_features(df):
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
