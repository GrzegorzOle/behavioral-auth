"""ONNX inference runtime for the behavioural autoencoder.

Loads the latest fused sequence from DuckDB, normalises it with the saved
scaler, runs the ONNX model, and returns the MSE reconstruction error as a
raw anomaly score.
"""

from pathlib import Path
import json
import numpy as np
import onnxruntime as ort
from behavioral_auth.config import load_settings


def latest_session(conn) -> str | None:
    """Return the session_id of the most recently started session, or None."""
    row = conn.execute(
        'SELECT session_id FROM sessions ORDER BY started_at DESC LIMIT 1'
    ).fetchone()
    return row[0] if row else None


def latest_sequence(conn, session_id: str) -> tuple | None:
    """Return (seq_end_ns, data_json) for the newest sequence of *session_id*."""
    row = conn.execute(
        'SELECT seq_end_ns, data_json FROM fused_sequences '
        'WHERE session_id = ? ORDER BY seq_end_ns DESC LIMIT 1',
        [session_id],
    ).fetchone()
    return row if row else None


def latest_sequence_any(conn) -> tuple | None:
    """Return (seq_end_ns, data_json) for the newest sequence across all sessions."""
    row = conn.execute(
        'SELECT seq_end_ns, data_json FROM fused_sequences ORDER BY seq_end_ns DESC LIMIT 1'
    ).fetchone()
    return row if row else None


def predict_behavioral(conn) -> tuple | None:
    """Run the ONNX autoencoder on the latest sequence and return MSE error.

    Returns:
        (session_id, seq_end_ns, mse_error) or None if model/data is missing.
    """
    cfg = load_settings()
    meta_path   = Path(cfg.model.metadata_path)
    scaler_path = Path(cfg.features.scaler_path)
    model_path  = Path(cfg.model.model_path)
    if not meta_path.exists() or not scaler_path.exists() or not model_path.exists():
        return None
    scaler = json.loads(scaler_path.read_text())

    # Try current session first, fall back to any session
    sid = latest_session(conn)
    row = latest_sequence(conn, sid) if sid else None

    if not row:
        row = latest_sequence_any(conn)
        sid_row = conn.execute(
            'SELECT session_id FROM fused_sequences ORDER BY seq_end_ns DESC LIMIT 1'
        ).fetchone()
        sid = sid_row[0] if sid_row else None

    if not row or not sid:
        return None

    seq_end_ns, data_json = row
    X    = np.array(json.loads(data_json), dtype=np.float32)
    mean = np.array(scaler['mean'], dtype=np.float32)
    std  = np.array(scaler['std'],  dtype=np.float32)
    X    = (X - mean) / std
    Xn   = np.transpose(X[np.newaxis, ...], (0, 2, 1))
    sess  = ort.InferenceSession(str(model_path), providers=['CPUExecutionProvider'])
    recon = sess.run(['recon'], {'input': Xn})[0][0]
    target     = Xn[0, :, -1]
    behavioral = float(np.mean((recon - target) ** 2))
    return sid, seq_end_ns, behavioral


