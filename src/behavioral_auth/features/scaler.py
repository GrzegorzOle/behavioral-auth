"""Feature scaler: fit a per-column z-score normaliser and persist it to JSON.

The scaler is fitted once on the training dataset and then applied at both
training time and inference time to keep the feature distributions comparable.
"""

import json
from pathlib import Path
import numpy as np


def fit_and_save_scaler(X: 'np.ndarray', path: str) -> tuple:
    """Compute per-feature mean and std from *X*, save to *path* as JSON.

    Args:
        X:    3-D array of shape (n_sequences, seq_len, n_features).
        path: Output JSON file path.

    Returns:
        (mean, std) arrays of shape (n_features,).
    """
    flat = X.reshape(-1, X.shape[-1])
    mean = flat.mean(axis=0)
    std = flat.std(axis=0) + 1e-6  # avoid division by zero
    Path(path).write_text(json.dumps({'mean': mean.tolist(), 'std': std.tolist()}))
    return mean, std


def apply_scaler(X: 'np.ndarray', mean: 'np.ndarray', std: 'np.ndarray') -> 'np.ndarray':
    """Standardise *X* using pre-computed *mean* and *std*."""
    return (X - mean) / std
