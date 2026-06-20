from pathlib import Path
import json
import numpy as np
import onnxruntime as ort
from behavioral_auth.config import load_settings

def latest_session(conn):
    row = conn.execute('SELECT session_id FROM sessions ORDER BY started_at DESC LIMIT 1').fetchone()
    return row[0] if row else None

def latest_sequence(conn, session_id):
    row = conn.execute('SELECT seq_end_ns, data_json FROM fused_sequences WHERE session_id = ? ORDER BY seq_end_ns DESC LIMIT 1', [session_id]).fetchone()
    return row if row else None

def latest_sequence_any(conn):
    """Return the most recent sequence from any session."""
    row = conn.execute(
        'SELECT seq_end_ns, data_json FROM fused_sequences ORDER BY seq_end_ns DESC LIMIT 1'
    ).fetchone()
    return row if row else None

def predict_behavioral(conn):
    cfg = load_settings()
    meta_path = Path(cfg.model.metadata_path)
    scaler_path = Path(cfg.features.scaler_path)
    model_path = Path(cfg.model.model_path)
    if not meta_path.exists() or not scaler_path.exists() or not model_path.exists():
        return None
    scaler = json.loads(scaler_path.read_text())

    # Try current session first, fall back to any session
    sid = latest_session(conn)
    if sid:
        row = latest_sequence(conn, sid)
    else:
        row = None

    if not row:
        row = latest_sequence_any(conn)
        # Use a placeholder session_id when falling back
        sid = conn.execute(
            'SELECT session_id FROM fused_sequences ORDER BY seq_end_ns DESC LIMIT 1'
        ).fetchone()
        sid = sid[0] if sid else None

    if not row or not sid:
        return None

    seq_end_ns, data_json = row
    X = np.array(json.loads(data_json), dtype=np.float32)
    mean = np.array(scaler['mean'], dtype=np.float32)
    std  = np.array(scaler['std'],  dtype=np.float32)
    X = (X - mean) / std
    Xn = np.transpose(X[np.newaxis, ...], (0, 2, 1))
    sess = ort.InferenceSession(str(model_path), providers=['CPUExecutionProvider'])
    recon = sess.run(['recon'], {'input': Xn})[0][0]
    target = Xn[0, :, -1]
    behavioral = float(np.mean((recon - target) ** 2))
    return sid, seq_end_ns, behavioral


