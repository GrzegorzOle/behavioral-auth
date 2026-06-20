import json
from pathlib import Path
import numpy as np

def fit_and_save_scaler(X, path: str):
    flat = X.reshape(-1, X.shape[-1])
    mean = flat.mean(axis=0)
    std = flat.std(axis=0) + 1e-6
    Path(path).write_text(json.dumps({'mean': mean.tolist(), 'std': std.tolist()}))
    return mean, std

def apply_scaler(X, mean, std):
    return (X - mean) / std
