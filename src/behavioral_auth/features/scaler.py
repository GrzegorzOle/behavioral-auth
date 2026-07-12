"""Per-feature z-score scaler, persisted to JSON.

Fitted on the training split only (never on the holdout — that would leak the
very data the promotion gate is meant to judge) and reapplied at scoring time.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def fit_scaler(X: np.ndarray, std_floor: float = 1e-3) -> dict:
    """Compute the scaler for *X*, shaped (n_sequences, seq_len, n_features).

    A feature that never varies during enrolment — is_weekend if you enrol on
    weekdays, say — has std ~0. Dividing by it would multiply the first real
    deviation by ~1/epsilon and blow the reconstruction error up on its own,
    which reads as an intruder. The floor makes that impossible; `constant`
    records which features hit it so the condition stays visible.
    """
    flat = X.reshape(-1, X.shape[-1])
    mean = flat.mean(axis=0)
    raw_std = flat.std(axis=0)
    std = np.maximum(raw_std, std_floor)
    return {
        'mean': mean.tolist(),
        'std': std.tolist(),
        'constant': (raw_std < std_floor).tolist(),
    }


def save_scaler(scaler: dict, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(scaler))


def load_scaler(path: str) -> dict:
    return json.loads(Path(path).read_text())


def apply_scaler(X: np.ndarray, scaler: dict) -> np.ndarray:
    """Standardise *X* with a fitted scaler dict."""
    mean = np.asarray(scaler['mean'], dtype=np.float32)
    std = np.asarray(scaler['std'], dtype=np.float32)
    return (X - mean) / std
